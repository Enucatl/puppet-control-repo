#!/usr/bin/env bash

set -euo pipefail

. "$(dirname "$0")/config.sh"

if [ -f .env ]; then
  set -o allexport
  source .env
  set +o allexport
fi
export VAULT_CACERT=/etc/ssl/certs/ca-certificates.crt

vault policy write admin - <<'EOF'
# Full administration, including new secrets engines and auth methods.
# This does not grant the root policy or replace unseal-key quorum operations.
path "*" {
  capabilities = ["create", "read", "update", "patch", "delete", "list", "sudo"]
}

# More specific rules in the default policy otherwise mask the wildcard.
path "sys/leases/lookup" {
  capabilities = ["create", "read", "update", "patch", "delete", "list", "sudo"]
}
path "identity/entity/id/{{identity.entity.id}}" {
  capabilities = ["create", "read", "update", "patch", "delete", "list", "sudo"]
}
path "identity/entity/name/{{identity.entity.name}}" {
  capabilities = ["create", "read", "update", "patch", "delete", "list", "sudo"]
}
EOF

vault write auth/ldap/groups/admins policies=admin,default
