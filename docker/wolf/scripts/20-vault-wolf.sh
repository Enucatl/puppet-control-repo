#!/usr/bin/env bash

set -euo pipefail

export VAULT_CACERT="${VAULT_CACERT:-/etc/ssl/certs/ca-certificates.crt}"

WOLF_CERT_AUTH_ROLE="${WOLF_CERT_AUTH_ROLE:-wolf}"
WOLF_CERT_COMMON_NAME="${WOLF_CERT_COMMON_NAME:-wolf.docker.home.arpa}"
WOLF_CERT_CA="${WOLF_CERT_CA:-/usr/local/share/ca-certificates/home-arpa/vault_intermediate.crt}"

vault policy write wolf - <<'EOF'
path "kv/data/wolf" {
  capabilities = ["read"]
}

path "auth/token/lookup-self" {
  capabilities = ["read"]
}
EOF

vault auth list 2>/dev/null | grep -q '^cert/' || vault auth enable cert

vault write "auth/cert/certs/${WOLF_CERT_AUTH_ROLE}" \
  certificate=@"$WOLF_CERT_CA" \
  token_policies="wolf" \
  allowed_common_names="$WOLF_CERT_COMMON_NAME" \
  token_ttl=15m

cat <<'EOF'
Vault policy and cert auth are configured.
Write proxmox-cortex and proxmox-cortex-api-token in kv/wolf
EOF
