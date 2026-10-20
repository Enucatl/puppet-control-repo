"""Build desktop cloud-init configuration without shell template expansion."""

import json
from pathlib import Path


def build_cloud_config(
    token: str, certname: str, puppet_server: str, domain_suffix: str, ca_bundle: str
) -> dict[str, object]:
    """Return cloud-init data with literal enrollment arguments and script content."""
    hostname = certname.split(".", 1)[0]
    identity = {
        "hostname": hostname,
        "fqdn": certname,
        "preserve_hostname": False,
        "prefer_fqdn_over_hostname": False,
        "manage_etc_hosts": True,
    }
    return {
        **identity,
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
                "path": "/etc/cloud/cloud.cfg.d/99-hostname.cfg",
                "permissions": "0644",
                "content": json.dumps(identity) + "\n",
            },
            {
                "path": "/usr/local/bin/configure-puppet.sh",
                "permissions": "0755",
                "content": Path(__file__).with_name("configure-puppet.sh").read_text(),
            },
            {
                "path": "/usr/local/bin/install-openvox.sh",
                "permissions": "0755",
                "content": Path(__file__).with_name("install-openvox.sh").read_text(),
            },
            {
                "path": "/etc/puppetlabs/puppet/ssl/certs/ca.pem",
                "permissions": "0644",
                "content": ca_bundle,
            },
        ],
        "runcmd": [
            "set -e",
            "export DEBIAN_FRONTEND=noninteractive",
            ["systemctl", "restart", "systemd-networkd"],
            ["/lib/systemd/systemd-networkd-wait-online", "--timeout=60"],
            ["sleep", "5"],
            ["mkdir", "-p", "/run/sshd"],
            ["chmod", "755", "/run/sshd"],
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
                "vault",
            ],
            ["bash", "/usr/local/bin/install-openvox.sh"],
            [
                "/opt/puppetlabs/puppet/bin/ruby",
                "-ropenssl",
                "-e",
                "certificates = File.read(ARGV.fetch(0)).scan("
                "/-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----/m)"
                ".map { |pem| OpenSSL::X509::Certificate.new(pem) }; "
                "roots = certificates.select { |cert| cert.subject == cert.issuer && "
                "cert.verify(cert.public_key) && cert.extensions.any? { |extension| "
                "extension.oid == 'basicConstraints' && extension.value.include?('CA:TRUE') } }; "
                "abort 'Authenticated bundle must contain exactly one root CA' unless roots.one?; "
                "File.write(ARGV.fetch(1), roots.first.to_pem)",
                "/etc/puppetlabs/puppet/ssl/certs/ca.pem",
                "/usr/local/share/ca-certificates/atlas-infrastructure.crt",
            ],
            ["update-ca-certificates"],
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
