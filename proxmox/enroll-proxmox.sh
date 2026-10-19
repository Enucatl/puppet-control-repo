#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/config.sh"
source "$SCRIPT_DIR/lib.sh"

# --- 1. LOAD ENVIRONMENT VARIABLES ---
load_env

: "${VAULT_TOKEN:?VAULT_TOKEN is not set}"
: "${VAULT_ADDR:?VAULT_ADDR is not set}"
: "${OPENVOX_CA_CERT:?Set authenticated OpenVox CA bundle path}"

# Refuse incompatible trust/identity before changing an enrolled host.
CERTNAME=$(hostname -f)
trust=/etc/puppetlabs/puppet/ssl/certs/ca.pem
test -s "$OPENVOX_CA_CERT"
if [[ -e $trust ]]; then
  cmp -s "$OPENVOX_CA_CERT" "$trust" || {
    echo 'Existing CA trust differs; use the explicit identity migration procedure.' >&2; exit 1;
  }
fi
if [[ -x /opt/puppetlabs/bin/puppet || -d /etc/puppetlabs/puppet/ssl/private_keys ]]; then
  "$SCRIPT_DIR/configure-puppet.sh" --preflight "$CERTNAME"
fi

# --- 2. GENERATE VAULT TOKEN ---
create_vault_token "$CERTNAME"

# --- 3. INSTALL THE ACCEPTED NATIVE OPENVOX PACKAGE ---
systemctl mask --runtime --now puppet.service
bash "$SCRIPT_DIR/install-openvox.sh"
if [[ ! -e $trust ]]; then
  install -D -m 0644 "$OPENVOX_CA_CERT" "$trust"
fi

# --- 5. CONFIGURE AND RUN PUPPET ---
"$SCRIPT_DIR/configure-puppet.sh" proxmox "$VM_TOKEN" "$CERTNAME" "$PUPPET_SERVER"
