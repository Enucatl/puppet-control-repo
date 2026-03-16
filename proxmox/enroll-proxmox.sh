#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/config.sh"
source "$SCRIPT_DIR/lib.sh"

# --- 1. LOAD ENVIRONMENT VARIABLES ---
load_env

: "${VAULT_TOKEN:?VAULT_TOKEN is not set}"
: "${VAULT_ADDR:?VAULT_ADDR is not set}"
: "${FORGE_KEY:?FORGE_KEY is not set}"

# --- 2. GENERATE VAULT TOKEN ---
create_vault_token

# --- 3. CONFIGURE REPO AUTHENTICATION ---
mkdir -p /etc/apt/auth.conf.d/
cat <<EOF > /etc/apt/auth.conf.d/puppet-core.conf
machine apt-puppetcore.puppet.com
login forge-key
password ${FORGE_KEY}
EOF
chmod 0600 /etc/apt/auth.conf.d/puppet-core.conf

# --- 4. INSTALL PUPPET AGENT ---
wget -O /tmp/puppet.deb https://apt-puppetcore.puppet.com/public/puppet8-release-noble.deb
dpkg -i /tmp/puppet.deb
rm /tmp/puppet.deb
apt-get update
apt-get install -y puppet-agent

# --- 5. CONFIGURE AND RUN PUPPET ---
"$SCRIPT_DIR/configure-puppet.sh" proxmox "$VM_TOKEN" "$PUPPET_SERVER"
