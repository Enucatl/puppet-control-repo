#!/usr/bin/env bash

set -euo pipefail

if [ -f .env ]; then
  set -o allexport
  source .env
  set +o allexport
fi

export VAULT_CACERT=/etc/ssl/certs/ca-certificates.crt

vault policy write puppet - <<EOF
path "kv/data/puppet" {
    capabilities = ["read"]
}

path "kv/data/wolf" {
    capabilities = ["read"]
}

# Allow issuing certificates
path "pki_int/issue/general" {
    capabilities = ["create", "update"]
}

path "auth/token/lookup-self" {
  capabilities = ["read"]
}
EOF

# Bootstrap credentials have no runtime secret access and are never granted to
# the Puppet certificate auth role.
vault policy write puppet-enrollment - <<'EOF'
path "auth/token/lookup-self" {
  capabilities = ["read"]
}
EOF
