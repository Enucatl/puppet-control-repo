import subprocess
import sys
import time
import os

import click
import textwrap


def wait_for_port(host, port, timeout_secs, sleep_interval=2):
    """
    Loops checking for a port using Netcat via subprocess.
    """
    max_attempts = timeout_secs // sleep_interval
    count = 0

    # We use -w 1 for a 1-second timeout per connection attempt
    cmd = f"nc -z -w 1 {host} {port}"

    while count < max_attempts:
        try:
            # check_call returns 0 on success, raises CalledProcessError on failure
            subprocess.check_call(cmd, shell=True, stderr=subprocess.DEVNULL)
            return True
        except subprocess.CalledProcessError:
            time.sleep(sleep_interval)
            count += 1

    return False


@click.command()
@click.option("--mac", default="30:56:0f:5e:a9:de", help="MAC Address for Wake-on-LAN")
@click.option(
    "--dropbear-host",
    default="dropbear.proxmox-cortex.home.arpa",
    help="Hostname/IP for Dropbear SSH",
)
@click.option(
    "--main-host", default="complex.home.arpa", help="Hostname/IP of the VM (Main OS)"
)
@click.option(
    "--proxmox-host",
    default="proxmox-cortex.home.arpa",
    help="Hostname/IP of the Proxmox Hypervisor",
)
@click.option(
    "--broadcast", default="10.0.0.255", help="Subnet broadcast address for WoL"
)
@click.option("--vm-id", default=200, help="ID of the Proxmox VM to start")
def main(mac, dropbear_host, main_host, proxmox_host, broadcast, vm_id):
    """
    Automates the process of waking a Proxmox server, unlocking the ZFS root via Dropbear,
    starting a specific VM, and launching Sunshine.
    """

    # 1. Retrieve Password from Vault
    click.echo("[-] Retrieving password from Vault...")
    try:
        # check_output is required here to capture the string; check_call only captures exit code.
        server_pass_bytes = subprocess.check_output(
            "vault kv get -field=pve-desktop kv/bitwarden", shell=True
        )
        server_pass = server_pass_bytes.decode("utf-8").strip()
    except subprocess.CalledProcessError:
        click.echo(
            "[!] Error: Could not retrieve password from Vault. Exiting.", err=True
        )
        sys.exit(1)

    if not server_pass:
        click.echo("[!] Error: Password variable is empty. Exiting.", err=True)
        sys.exit(1)

    click.echo("[+] Password retrieved successfully.")

    # 2. Send Wake-on-LAN
    click.echo(f"[-] Sending Wake-on-LAN packet to {mac}...")
    try:
        subprocess.check_call(f"wakeonlan -i '{broadcast}' '{mac}'", shell=True)
    except subprocess.CalledProcessError:
        click.echo("[!] Failed to send WoL.", err=True)
        sys.exit(1)

    # 3. Connect to Dropbear and Unlock
    click.echo(f"[-] Waiting for Dropbear SSH ({dropbear_host}) to become available...")

    # Wait max 120 seconds for Dropbear (Port 2222)
    if not wait_for_port(dropbear_host, 2222, 120, 2):
        click.echo("[!] Timed out waiting for Dropbear.", err=True)
        sys.exit(1)

    click.echo("[+] Dropbear is up. Attempting to unlock via expect...")

    # Construct the expect script.
    # Note on f-strings:
    # {variable} is Python interpolation.
    # {{ }} is a literal brace for the Tcl/Expect script.
    # \$env matches the environment variable in Tcl.
    expect_script = textwrap.dedent(f"""
        log_user 1
        set timeout 15
        spawn ssh -p 2222 {dropbear_host}
        expect {{
            "password for rpool/ROOT" {{
                send "$env(SERVER_PASS)\\r"
                exp_continue
            }}
            "Unlocking complete" {{
                puts "\\nSuccess: Unlock detected."
                exp_continue
            }}
            timeout {{
                puts "\\nError: Expect timed out."
                exit 1
            }}
            eof {{
                puts "\\nConnection closed by host. Unlock sequence finished."
                exit 0
            }}
        }}
    """)

    env_vars = os.environ.copy()
    env_vars["SERVER_PASS"] = server_pass

    # Using subprocess.run with 'input' is much safer than shell Here-Docs
    result = subprocess.run(
        ["expect", "-"], input=expect_script, text=True, env=env_vars
    )

    if result.returncode != 0:
        click.echo("[!] Failed to send unlock command.")
        sys.exit(1)

    # 4. Connect to Main OS and start Sunshine
    click.echo(f"[-] Waiting for Proxmox Host ({proxmox_host}) to boot...")

    # Wait max 180 seconds for Proxmox SSH (Port 22)
    if not wait_for_port(proxmox_host, 22, 180, 5):
        click.echo("[!] Timed out waiting for Proxmox Host.", err=True)
        sys.exit(1)

    click.echo(f"[+] Proxmox Host is up. Starting VM {vm_id}...")
    try:
        subprocess.check_call(f"ssh '{proxmox_host}' 'qm start {vm_id}'", shell=True)
    except subprocess.CalledProcessError:
        click.echo("[!] Failed to start VM.", err=True)
        sys.exit(1)

    # Wait max 180 seconds for VM SSH (Port 22)
    click.echo(f"[-] Waiting for VM ({main_host}) to come online...")
    if not wait_for_port(main_host, 22, 180, 5):
        click.echo("[!] Timed out waiting for Main Host (VM).", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
