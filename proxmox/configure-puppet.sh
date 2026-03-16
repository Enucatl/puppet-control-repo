#!/bin/bash
set -euo pipefail
# Shared Puppet enrollment script
# Usage: configure-puppet.sh <node_type> <vault_token> [puppet_server]

NODE_TYPE="${1:?Usage: configure-puppet.sh <node_type> <vault_token> [puppet_server]}"
VAULT_TOKEN="${2:?Usage: configure-puppet.sh <node_type> <vault_token> [puppet_server]}"
PUPPET_SERVER="${3:-docker.home.arpa}"

# CSR Attributes (for autosigning)
mkdir -p /etc/puppetlabs/puppet/
cat <<EOF > /etc/puppetlabs/puppet/csr_attributes.yaml
custom_attributes:
  1.2.840.113549.1.9.7: "${VAULT_TOKEN}"
EOF
chmod 0640 /etc/puppetlabs/puppet/csr_attributes.yaml

# Custom Facts
mkdir -p /etc/facter/facts.d/
echo "node_type=${NODE_TYPE}" > /etc/facter/facts.d/node_type.txt

# Puppet Configuration
/opt/puppetlabs/bin/puppet config set server "$PUPPET_SERVER" --section main
/opt/puppetlabs/bin/puppet config set certificate_revocation leaf --section agent

# Enable and run
systemctl enable puppet
/opt/puppetlabs/bin/puppet resource service puppet enable=true
/opt/puppetlabs/bin/puppet agent --test --waitforlock 300 || true
/opt/puppetlabs/bin/puppet resource service puppet ensure=running
