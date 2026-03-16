#!/usr/bin/env bash

set -euo pipefail

if [ -f .env ]; then
  set -o allexport
  source .env
  set +o allexport
fi
export VAULT_CACERT=/etc/ssl/certs/ca-certificates.crt

vault policy write airflow - <<EOF
path "kv/data/airflow/connections/*" {
  capabilities = ["read", "list"]
}

path "kv/data/airflow/variables/*" {
  capabilities = ["read", "list"]
}
EOF

vault write auth/ldap/groups/airflow policies=airflow
