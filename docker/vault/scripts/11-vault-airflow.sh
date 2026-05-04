#!/usr/bin/env bash

set -euo pipefail

. "$(dirname "$0")/config.sh"

if [ -f .env ]; then
  set -o allexport
  source .env
  set +o allexport
fi
export VAULT_CACERT=/etc/ssl/certs/ca-certificates.crt

AIRFLOW_CERT_AUTH_ROLE="${AIRFLOW_CERT_AUTH_ROLE:-airflow}"
AIRFLOW_CERT_DNS_SAN="${AIRFLOW_CERT_DNS_SAN:-airflow.docker.${DOMAIN}}"
AIRFLOW_CERT_CA="${AIRFLOW_CERT_CA:-/usr/local/share/ca-certificates/home-arpa/vault_intermediate.crt}"

vault policy write airflow - <<EOF
path "kv/data/airflow/connections/*" {
  capabilities = ["read", "list"]
}

path "kv/data/airflow/variables/*" {
  capabilities = ["read", "list"]
}
EOF

vault auth list 2>/dev/null | grep -q '^cert/' || vault auth enable cert

vault write "auth/cert/certs/${AIRFLOW_CERT_AUTH_ROLE}" \
  certificate=@"$AIRFLOW_CERT_CA" \
  token_policies="airflow" \
  allowed_dns_sans="$AIRFLOW_CERT_DNS_SAN" \
  token_ttl=15m
