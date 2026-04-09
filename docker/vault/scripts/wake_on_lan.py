import json
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
    "--main-host",
    default=None,
    help="Hostname/IP to wait for after starting a VM or container (omit to skip)",
)
@click.option(
    "--proxmox-host",
    default="proxmox-cortex.home.arpa",
    help="Hostname/IP of the Proxmox Hypervisor",
)
@click.option(
    "--broadcast", default="10.0.0.255", help="Subnet broadcast address for WoL"
)
@click.option("--vm-id", default=None, type=int, help="ID of the Proxmox VM to start (omit to skip)")
@click.option("--ct-id", default=None, type=int, help="ID of the Proxmox LXC container to start (omit to skip)")
@click.option("--shutdown-delay", default=0, help="Schedule shutdown N minutes after boot (0 = no shutdown)")
def main(mac, dropbear_host, main_host, proxmox_host, broadcast, vm_id, ct_id, shutdown_delay):
    """
    Automates waking a Proxmox server, unlocking the ZFS root via Dropbear,
    and optionally starting a VM or LXC container.
    """

    # 0. Check if server is already on
    already_on = subprocess.call(
        f"nc -z -w 2 {proxmox_host} 22", shell=True, stderr=subprocess.DEVNULL
    ) == 0
    if already_on:
        click.echo("[+] Server is already running. Skipping wake sequence.")
        return

    vault_env = os.environ.copy()
    vault_env.setdefault("VAULT_ADDR", "https://hcv.home.arpa:8200")
    vault_env.setdefault("VAULT_CACERT", "/etc/ssl/certs/ca-certificates.crt")

    # 1. Retrieve Password from Vault (with cert auth fallback)
    click.echo("[-] Retrieving password from Vault...")
    try:
        server_pass_bytes = subprocess.check_output(
            "vault kv get -field=proxmox-cortex kv/puppet", shell=True, env=vault_env,
            stderr=subprocess.DEVNULL,
        )
        server_pass = server_pass_bytes.decode("utf-8").strip()
    except subprocess.CalledProcessError:
        click.echo("[-] Vault read failed, attempting cert auth...")
        try:
            token_bytes = subprocess.check_output(
                [
                    "vault", "login", "-method=cert", "-format=json",
                    "-client-cert=/etc/puppetlabs/puppet/ssl/certs/docker.home.arpa.pem",
                    "-client-key=/etc/puppetlabs/puppet/ssl/private_keys/docker.home.arpa.pem",
                ],
                env=vault_env,
                stderr=subprocess.DEVNULL,
            )
            vault_env["VAULT_TOKEN"] = json.loads(token_bytes)["auth"]["client_token"]
        except (subprocess.CalledProcessError, KeyError, json.JSONDecodeError) as e:
            click.echo(f"[!] Vault cert auth failed: {e}", err=True)
            sys.exit(1)
        try:
            server_pass_bytes = subprocess.check_output(
                "vault kv get -field=proxmox-cortex kv/puppet", shell=True, env=vault_env,
            )
            server_pass = server_pass_bytes.decode("utf-8").strip()
        except subprocess.CalledProcessError:
            click.echo("[!] Error: Could not retrieve password from Vault. Exiting.", err=True)
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
        spawn ssh -p 2222 -o StrictHostKeyChecking=accept-new {dropbear_host}
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

    if vm_id is not None:
        click.echo(f"[+] Proxmox Host is up. Starting VM {vm_id}...")
        try:
            subprocess.check_call(f"ssh '{proxmox_host}' 'qm start {vm_id}'", shell=True)
        except subprocess.CalledProcessError:
            click.echo("[!] Failed to start VM.", err=True)
            sys.exit(1)

    if ct_id is not None:
        click.echo(f"[+] Proxmox Host is up. Starting container {ct_id}...")
        try:
            subprocess.check_call(f"ssh '{proxmox_host}' 'pct start {ct_id}'", shell=True)
        except subprocess.CalledProcessError:
            click.echo("[!] Failed to start container.", err=True)
            sys.exit(1)

    if vm_id is None and ct_id is None:
        click.echo("[+] Proxmox Host is up.")
    elif main_host:
        # Wait max 180 seconds for main host SSH (Port 22)
        click.echo(f"[-] Waiting for main host ({main_host}) to come online...")
        if not wait_for_port(main_host, 22, 180, 5):
            click.echo("[!] Timed out waiting for main host.", err=True)
            sys.exit(1)

    # Schedule shutdown if requested
    if shutdown_delay > 0:
        click.echo(f"[-] Scheduling shutdown in {shutdown_delay} minutes...")
        try:
            subprocess.check_call(
                f"ssh '{proxmox_host}' \"shutdown +{shutdown_delay} 'Automated shutdown'\"",
                shell=True,
            )
        except subprocess.CalledProcessError:
            click.echo("[!] Failed to schedule shutdown.", err=True)
            sys.exit(1)


if __name__ == "__main__":
    main()
