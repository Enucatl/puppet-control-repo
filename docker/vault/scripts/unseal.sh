#!/usr/bin/env bash

set +x
set -euo pipefail

UNSEAL_KEY_FILE=${UNSEAL_KEY_FILE:-/unseal/key}

while true; do
    if ! status=$(curl --silent --show-error --connect-timeout 5 --max-time 10 "$VAULT_ADDR/v1/sys/health"); then
        echo '[Unsealer] Vault unreachable.'
    elif ! jq -e '.initialized == true' <<< "$status" >/dev/null; then
        echo '[Unsealer] Vault needs initialization; run the vault-init setup service.'
    elif jq -e '.sealed == true' <<< "$status" >/dev/null; then
        if [ ! -s "$UNSEAL_KEY_FILE" ]; then
            echo '[Unsealer] Unseal key file is missing or empty.'
        elif response=$(jq -n --rawfile key "$UNSEAL_KEY_FILE" '{key: ($key | rtrimstr("\n"))}' |
            curl --fail --silent --show-error --connect-timeout 5 --max-time 10 \
                --header 'Content-Type: application/json' --data-binary @- "$VAULT_ADDR/v1/sys/unseal") &&
            jq -e '.sealed == false' <<< "$response" >/dev/null; then
            echo '[Unsealer] Vault unsealed.'
        else
            echo '[Unsealer] Unseal failed; check Vault health and the key file.'
        fi
    else
        echo '[Unsealer] Vault is initialized and unsealed.'
    fi
    sleep 120
done
