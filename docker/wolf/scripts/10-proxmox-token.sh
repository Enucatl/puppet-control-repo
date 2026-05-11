#!/usr/bin/env bash

set -euo pipefail

PVE_USER="${WOLF_PVE_USER:-wolf@pve}"
PVE_TOKEN_ID="${WOLF_PVE_TOKEN_ID:-wolf}"
PVE_VM_ROLE_NAME="${WOLF_PVE_VM_ROLE_NAME:-WolfVmControl}"
PVE_NODE_ROLE_NAME="${WOLF_PVE_NODE_ROLE_NAME:-WolfNodePower}"
PVE_VM_ID="${WOLF_VM_ID:-200}"
PVE_NODE="${WOLF_PROXMOX_NODE:-proxmox-cortex}"

if ! command -v pveum >/dev/null 2>&1; then
  echo "pveum not found" >&2
  exit 127
fi

if ! pveum user list | awk -v user="$PVE_USER" '
  $1 == user || $2 == user {
    found = 1
  }
  END {
    exit(found ? 0 : 1)
  }
'; then
  pveum user add "$PVE_USER" --comment="Wolf power control service" >/dev/null
fi

if ! pveum role list | awk -v role="$PVE_VM_ROLE_NAME" '
  $1 == role {
    found = 1
  }
  END {
    exit(found ? 0 : 1)
  }
'; then
  pveum role add "$PVE_VM_ROLE_NAME" \
    --privs="VM.PowerMgmt VM.Console VM.Audit" >/dev/null
fi

if ! pveum role list | awk -v role="$PVE_NODE_ROLE_NAME" '
  $1 == role {
    found = 1
  }
  END {
    exit(found ? 0 : 1)
  }
'; then
  pveum role add "$PVE_NODE_ROLE_NAME" \
    --privs="Sys.PowerMgmt Sys.Audit" >/dev/null
fi

if pveum user token list "$PVE_USER" 2>/dev/null \
  | grep -Eq "(^|[[:space:]│])${PVE_TOKEN_ID}([[:space:]│]|$)"; then
  TOKEN_OUTPUT=""
else
  echo "Create or copy the token secret now:"
  TOKEN_OUTPUT="$(pveum user token add "$PVE_USER" "$PVE_TOKEN_ID" -privsep 1)"
  printf '%s\n' "$TOKEN_OUTPUT"
  echo
fi

pveum acl modify "/vms/${PVE_VM_ID}" -token "${PVE_USER}!${PVE_TOKEN_ID}" \
  -role "$PVE_VM_ROLE_NAME" >/dev/null
pveum acl modify "/nodes/${PVE_NODE}" -token "${PVE_USER}!${PVE_TOKEN_ID}" \
  -role "$PVE_NODE_ROLE_NAME" >/dev/null

TOKEN_SECRET="$(printf '%s\n' "$TOKEN_OUTPUT" | awk -F'│' '/ value / { gsub(/^[[:space:]]+|[[:space:]]+$/, "", $3); print $3 }')"
if [ -n "$TOKEN_SECRET" ]; then
  echo "Store this full value in Vault at kv/wolf field proxmox-cortex-api-token:"
  echo "${PVE_USER}!${PVE_TOKEN_ID}=${TOKEN_SECRET}"
elif [ -n "$TOKEN_OUTPUT" ]; then
  echo "Store the full value ${PVE_USER}!${PVE_TOKEN_ID}=<token-secret> in Vault at kv/wolf field proxmox-cortex-api-token."
else
  echo "Token ${PVE_USER}!${PVE_TOKEN_ID} already exists; Proxmox will not show its secret again."
  echo "If the secret is not already in Vault, remove and recreate the token or create a new token id."
fi
