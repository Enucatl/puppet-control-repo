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

entry_exists() {
  local output="$1"
  local needle="$2"

  printf '%s' "$output" | python3 -c '
import json
import sys

needle = sys.argv[1]
try:
    payload = json.load(sys.stdin)
except json.JSONDecodeError:
    raise SystemExit(1)

if isinstance(payload, dict) and isinstance(payload.get("data"), list):
    items = payload["data"]
elif isinstance(payload, list):
    items = payload
else:
    items = []

for item in items:
    if not isinstance(item, dict):
        continue
    if needle in item.values():
        raise SystemExit(0)

raise SystemExit(1)
' "$needle"
}

if ! entry_exists "$(pveum user list --output-format json)" "$PVE_USER"; then
  pveum user add "$PVE_USER" --comment="Wolf power control service" >/dev/null
fi

if ! entry_exists "$(pveum role list --output-format json)" "$PVE_VM_ROLE_NAME"; then
  pveum role add "$PVE_VM_ROLE_NAME" \
    --privs="VM.PowerMgmt VM.Console VM.Audit" >/dev/null
fi

if ! entry_exists "$(pveum role list --output-format json)" "$PVE_NODE_ROLE_NAME"; then
  pveum role add "$PVE_NODE_ROLE_NAME" \
    --privs="Sys.PowerMgmt Sys.Audit" >/dev/null
fi

if entry_exists "$(pveum user token list "$PVE_USER" --output-format json)" "$PVE_TOKEN_ID"; then
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
