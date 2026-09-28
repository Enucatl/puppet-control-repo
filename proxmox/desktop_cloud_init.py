"""Build desktop cloud-init configuration without shell template expansion."""

from pathlib import Path


def build_cloud_config(
    token: str, certname: str, puppet_server: str, domain_suffix: str
) -> dict[str, object]:
    """Return cloud-init data with literal enrollment arguments and script content."""
    hostname = certname.split(".", 1)[0]
    hosts_entry = f"127.0.1.1 {hostname}.{domain_suffix} {hostname}"
    hosts_entry = (
        hosts_entry.replace("\\", "\\\\").replace("&", r"\&").replace("/", r"\/")
    )
    return {
        "output": {"all": "| tee -a /var/log/cloud-init-output.log /dev/ttyS0"},
        "bootcmd": [
            [
                "cloud-init-per",
                "instance",
                "puppet-enrollment-mask",
                "systemctl",
                "mask",
                "--runtime",
                "--now",
                "puppet.service",
            ]
        ],
        "packages": ["auto-apt-proxy", "gnupg"],
        "write_files": [
            {
                "path": "/usr/local/bin/configure-puppet.sh",
                "permissions": "0755",
                "content": Path(__file__).with_name("configure-puppet.sh").read_text(),
            }
        ],
        "runcmd": [
            "set -e",
            "export DEBIAN_FRONTEND=noninteractive",
            ["systemctl", "restart", "systemd-networkd"],
            ["/lib/systemd/systemd-networkd-wait-online", "--timeout=60"],
            ["sleep", "5"],
            ["mkdir", "-p", "/run/sshd"],
            ["chmod", "755", "/run/sshd"],
            ["sed", "-i", f"s/^127.0.1.1.*/{hosts_entry}/", "/etc/hosts"],
            ["install", "-m", "0755", "-d", "/etc/apt/keyrings"],
            "wget -O- https://apt.releases.hashicorp.com/gpg | "
            "gpg --dearmor -o /etc/apt/keyrings/hashicorp-archive-keyring.gpg",
            'echo "deb [signed-by=/etc/apt/keyrings/hashicorp-archive-keyring.gpg] '
            'https://apt.releases.hashicorp.com $(lsb_release -cs) main" | '
            "tee /etc/apt/sources.list.d/hashicorp.list",
            ["apt-get", "update"],
            [
                "apt-get",
                "install",
                "-y",
                "htop",
                "libnss-resolve",
                "linux-firmware",
                "linux-image-generic",
                "puppet-agent",
                "vault",
            ],
            ["apt-get", "install", "-y", "--install-recommends", "ubuntu-desktop"],
            ["systemctl", "mask", "systemd-networkd-wait-online.service"],
            [
                "/usr/local/bin/configure-puppet.sh",
                "desktop",
                token,
                certname,
                puppet_server,
            ],
            ["systemctl", "start", "qemu-guest-agent"],
            ["systemctl", "set-default", "graphical.target"],
            [
                "systemctl",
                "mask",
                "sleep.target",
                "suspend.target",
                "hibernate.target",
                "hybrid-sleep.target",
            ],
        ],
    }
