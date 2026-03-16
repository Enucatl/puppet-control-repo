#!/usr/bin/env bash

set -euo pipefail

. "$(dirname "$0")/config.sh"

if [ -f .env ]; then
  set -o allexport
  source .env
  set +o allexport
fi
export VAULT_CACERT=/usr/local/share/ca-certificates/home-arpa/vault_root.crt

vault audit list 2>/dev/null | grep -q '^file/' || vault audit enable file file_path=/vault/file/audit.log elide_list_responses=true
vault auth list 2>/dev/null | grep -q '^cert/' || vault auth enable cert

vault write auth/cert/certs/puppet \
    certificate=@/etc/puppetlabs/puppet/ssl/certs/ca.pem \
    policies="puppet" \
    allowed_dns_sans="*.${DOMAIN}" \
    ttl=15m

vault policy write puppet - <<EOF
path "kv/data/puppet" {
    capabilities = ["read"]
}
EOF

vault secrets list 2>/dev/null | grep -q '^kv/' || vault secrets enable -version=2 kv
