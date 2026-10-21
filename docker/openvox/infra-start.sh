#!/usr/bin/env bash
set -euo pipefail

# Startup never initializes Vault, imports a CA, or replaces an absent identity.
for path in /trust/root.pem /trust/openvox-chain.pem \
  /etc/puppetlabs/puppetserver/ca/ca_crl.pem \
  /etc/puppetlabs/code/environments/production/.r10k-deploy.json; do
  [[ -r $path && -s $path ]] || {
    printf 'Restore established OpenVox state before startup; missing: %s\n' "$path" >&2
    exit 1
  }
done
exec /usr/local/bin/openvox-server-entrypoint \
  /opt/puppetlabs/server/apps/puppetserver/cli/apps/foreground
