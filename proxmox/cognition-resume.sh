#!/bin/bash
set -euo pipefail
# Resume failed initial cloud-init without replacing the enrolled identity.
[[ $(id -u) == 0 && $(hostname -f) == cognition.home.arpa ]] || {
    echo 'Run as root on cognition.home.arpa.' >&2; exit 1;
}
bash /usr/local/bin/configure-puppet.sh --preflight cognition.home.arpa
test -s /etc/puppetlabs/puppet/ssl/certs/cognition.home.arpa.pem
test -f /var/lib/cloud/instance/scripts/runcmd
systemctl stop puppet
sed -i 's/gpg --dearmor -o \/usr\/share\/keyrings\/hashicorp-archive-keyring.gpg/gpg --batch --yes --dearmor -o \/usr\/share\/keyrings\/hashicorp-archive-keyring.gpg/' /var/lib/cloud/instance/scripts/runcmd
cloud-init single --name scripts-user --frequency always
systemctl restart cloud-final
cloud-init status --wait
