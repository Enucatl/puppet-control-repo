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
# Vault admin policy — based on HashiCorp recommendations
# https://support.hashicorp.com/hc/en-us/articles/42417725566483

# --- ACL policy management ---
path "sys/policies/acl/*" {
  capabilities = ["create", "read", "update", "delete", "list", "sudo"]
}
path "sys/policies/acl" {
  capabilities = ["list"]
}

# --- Secrets engine management ---
path "sys/mounts/*" {
  capabilities = ["create", "read", "update", "delete", "list"]
}
path "sys/mounts" {
  capabilities = ["read"]
}

# --- Auth method management ---
path "sys/auth/*" {
  capabilities = ["create", "read", "update", "delete", "list"]
}
path "sys/auth" {
  capabilities = ["read"]
}

# --- Audit device management ---
path "sys/audit/*" {
  capabilities = ["create", "read", "update", "delete", "list", "sudo"]
}
path "sys/audit" {
  capabilities = ["read", "list", "sudo"]
}

# --- System health and status ---
path "sys/health" {
  capabilities = ["read", "sudo"]
}
path "sys/seal" {
  capabilities = ["update", "sudo"]
}
path "sys/unseal" {
  capabilities = ["update", "sudo"]
}
path "sys/leader" {
  capabilities = ["read"]
}
path "sys/config/state/sanitized" {
  capabilities = ["read"]
}

# --- Token management ---
path "auth/token/*" {
  capabilities = ["create", "read", "update", "delete", "list", "sudo"]
}

# --- PKI engines (pki = root CA, pki_int = intermediate CA) ---
path "pki/*" {
  capabilities = ["create", "read", "update", "delete", "list", "sudo"]
}
path "pki_int/*" {
  capabilities = ["create", "read", "update", "delete", "list", "sudo"]
}

# --- KV v2 secrets engine ---
path "kv/*" {
  capabilities = ["create", "read", "update", "delete", "list"]
}

# --- LDAP auth configuration ---
path "auth/ldap/*" {
  capabilities = ["create", "read", "update", "delete", "list"]
}

# --- Certificate auth configuration ---
path "auth/cert/*" {
  capabilities = ["create", "read", "update", "delete", "list"]
}
EOF

vault write auth/ldap/groups/admins policies=admin,default
