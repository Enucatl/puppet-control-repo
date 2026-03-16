#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/config.sh"
source "$SCRIPT_DIR/lib.sh"

# --- 1. LOAD ENVIRONMENT VARIABLES ---
load_env

: "${VAULT_TOKEN:?VAULT_TOKEN is not set}"
: "${VAULT_ADDR:?VAULT_ADDR is not set}"

# --- 2. CONFIGURATION ---
DOWNLOAD_PATH="/tmp/checkmk_agent.deb"

# --- 3. FETCH SECRET FROM VAULT ---
echo "Fetching Secret from Vault..."
API_SECRET=$(curl -s \
    --header "X-Vault-Token: ${VAULT_TOKEN}" \
    "${VAULT_ADDR}/v1/kv/data/puppet" | jq -r '.data.data["checkmk::agent_registration_password"]')

# --- 4. DOWNLOAD THE BAKED AGENT ---
API_URL="https://${CMK_DOMAIN}/${CMK_SITE}/check_mk/api/1.0"

echo "Downloading baked auto-registration agent..."
curl -L -k -G --fail \
    --header "Authorization: Bearer ${CMK_REG_USER} ${API_SECRET}" \
    --header "Accept: application/octet-stream" \
    --data-urlencode "agent_type=generic" \
    --data-urlencode "folder_name=/auto/" \
    --data-urlencode "os_type=linux_deb" \
    -o $DOWNLOAD_PATH \
    "${API_URL}/domain-types/agent/actions/download_by_host/invoke"

apt install -y $DOWNLOAD_PATH

# --- 5. REGISTER AGENT UPDATER ---
echo "Registering agent updater with CheckMK..."
cmk-update-agent register \
    -s "${CMK_DOMAIN}" \
    -i "${CMK_SITE}" \
    -H "$(hostname)" \
    -p https \
    -U "${CMK_REG_USER}" \
    -P "${API_SECRET}"
