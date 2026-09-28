#!/usr/bin/env bash

set +x
set -euo pipefail
umask 077

INIT_FILE=${INIT_FILE:-/recovery/init.json}
ROOT_TOKEN_FILE=${ROOT_TOKEN_FILE:-/bootstrap/root-token}
UNSEAL_KEY_FILE=${UNSEAL_KEY_FILE:-/unseal/key}

# This one-off service is the only container with access to all three volumes.
mkdir -p "$(dirname "$INIT_FILE")" "$(dirname "$ROOT_TOKEN_FILE")" "$(dirname "$UNSEAL_KEY_FILE")"
chmod 700 "$(dirname "$INIT_FILE")" "$(dirname "$ROOT_TOKEN_FILE")" "$(dirname "$UNSEAL_KEY_FILE")"

status=$(curl --silent --show-error --connect-timeout 5 --max-time 10 "$VAULT_ADDR/v1/sys/health")
if jq -e '.initialized == false' <<< "$status" >/dev/null; then
    # Reserve the file before initializing. Never overwrite recovery material.
    (set -o noclobber; : > "$INIT_FILE")
    curl --fail --silent --show-error --connect-timeout 5 --max-time 30 \
        --header 'Content-Type: application/json' \
        --data '{"secret_shares":1,"secret_threshold":1}' \
        --output "$INIT_FILE" "$VAULT_ADDR/v1/sys/init"
    sync
elif ! jq -e '.initialized == true' <<< "$status" >/dev/null; then
    echo 'Cannot determine Vault initialization status.' >&2
    exit 1
fi

# Also permits recovering a split interrupted after the init response was saved.
jq -e '(.keys_base64 | length == 1) and (.keys_base64[0] | type == "string" and length > 0)
    and (.root_token | type == "string" and length > 0)' "$INIT_FILE" >/dev/null
for file in "$ROOT_TOKEN_FILE" "$UNSEAL_KEY_FILE"; do
    if [ -e "$file" ]; then
        echo 'Split credential files already exist; refusing to overwrite them.' >&2
        exit 1
    fi
done
jq -r '.root_token' "$INIT_FILE" > "$ROOT_TOKEN_FILE"
jq -r '.keys_base64[0]' "$INIT_FILE" > "$UNSEAL_KEY_FILE"
sync
echo 'Initialization saved privately; bootstrap token and unseal key stored separately.'
