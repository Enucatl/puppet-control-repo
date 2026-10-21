#!/usr/bin/env bash

set +x
set -euo pipefail

UNSEAL_KEY_FILE=${UNSEAL_KEY_FILE:-/unseal/key}

# Sealed Vault cannot serve fresh CRLs; its locally provisioned TLS key pin
# authenticates only these bootstrap requests, including after leaf expiry.
tls_options=()
if [[ -n ${VAULT_PINNED_PUBKEY:-} ]]; then
    [[ $VAULT_ADDR == https://* && -s $VAULT_PINNED_PUBKEY ]] || {
        echo '[Unsealer] HTTPS and an authenticated local public-key pin are required.' >&2
        exit 1
    }
    tls_options=(--insecure --pinnedpubkey "$VAULT_PINNED_PUBKEY" --tlsv1.3 --tls-max 1.3)
fi

if [[ ${1:-} == --healthcheck ]]; then
    curl "${tls_options[@]}" --fail --silent --show-error --connect-timeout 5 --max-time 10 \
        "$VAULT_ADDR/v1/sys/health" | jq -e '.initialized == true and .sealed == false' >/dev/null
    exit
fi

while true; do
    if ! status=$(curl "${tls_options[@]}" --silent --show-error --connect-timeout 5 --max-time 10 "$VAULT_ADDR/v1/sys/health"); then
        echo '[Unsealer] Vault unreachable.'
    elif ! jq -e '.initialized == true' <<< "$status" >/dev/null; then
        echo '[Unsealer] Vault is uninitialized; restore established data, or explicitly initialize a fresh installation.'
    elif jq -e '.sealed == true' <<< "$status" >/dev/null; then
        if [ ! -s "$UNSEAL_KEY_FILE" ]; then
            echo '[Unsealer] Unseal key file is missing or empty.'
        elif response=$(jq -n --rawfile key "$UNSEAL_KEY_FILE" '{key: ($key | rtrimstr("\n"))}' |
            curl "${tls_options[@]}" --fail --silent --show-error --connect-timeout 5 --max-time 10 \
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
