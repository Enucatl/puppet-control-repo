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

secret_value() {
  local field="$1"
  local value="$2"

  if [ -z "$value" ]; then
    value="$(vault kv get -field="$field" kv/wolf 2>/dev/null || true)"
  fi
  if [ -z "$value" ]; then
    value="$(vault kv get -field="$field" kv/puppet 2>/dev/null || true)"
  fi
  printf '%s' "$value"
}

DROPBEAR_PASSWORD="$(secret_value proxmox-cortex "${WOLF_DROPBEAR_PASSWORD:-}")"
PROXMOX_API_TOKEN="$(secret_value proxmox-cortex-api-token "${WOLF_PROXMOX_API_TOKEN:-}")"

if [ -n "$DROPBEAR_PASSWORD" ] || [ -n "$PROXMOX_API_TOKEN" ]; then
  vault kv put kv/wolf \
    proxmox-cortex="$DROPBEAR_PASSWORD" \
    proxmox-cortex-api-token="$PROXMOX_API_TOKEN" >/dev/null
fi

if [ -z "$DROPBEAR_PASSWORD" ] || [ -z "$PROXMOX_API_TOKEN" ]; then
  cat <<'EOF'
Vault policy and cert auth are configured.
Wolf secrets have been seeded in kv/wolf when values were provided or found in kv/puppet.

To seed or overwrite the Wolf secrets in kv/wolf, set:
  - WOLF_DROPBEAR_PASSWORD
  - WOLF_PROXMOX_API_TOKEN

Then rerun this script or write kv/wolf manually with vault kv put.
EOF
fi
