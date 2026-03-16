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

# Allow issuing certificates
path "pki_int/issue/general" {
    capabilities = ["create", "update"]
}

path "auth/token/lookup-self" {
  capabilities = ["read"]
}
EOF
