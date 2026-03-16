#!/usr/bin/env bash

set -u

# -----------------------------------------------------------------------------
# CONFIGURATION & PRE-FLIGHT
# -----------------------------------------------------------------------------
if ! command -v jq &> /dev/null; then
    echo "[Unsealer] Error: jq is not installed."
    exit 1
fi

echo "[Unsealer] Starting infinite supervision loop..."

# -----------------------------------------------------------------------------
# SUPERVISION LOOP
# -----------------------------------------------------------------------------
while true; do

    # 1. Connectivity Check
    # We use max-time to fail fast if connection hangs.
    HTTP_RESPONSE=$(curl -s --max-time 5 "$VAULT_ADDR/v1/sys/health")
    CURL_EXIT_CODE=$?

    # If curl fails entirely (connection refused, timeout), log and wait.
    if [ $CURL_EXIT_CODE -ne 0 ]; then
        echo "[Unsealer] Vault unreachable (curl code: $CURL_EXIT_CODE). Retrying in 60s..."
        sleep 60
        continue
    fi

    # 2. Initialization Check
    IS_INIT=$(echo "$HTTP_RESPONSE" | jq -r '.initialized')

    if [ "$IS_INIT" = "false" ]; then
        echo "[Unsealer] Vault is NOT initialized. Initializing..."
        
        # 1 Key Share, 1 Threshold (Lab Mode)
        INIT_PAYLOAD='{"secret_shares": 1, "secret_threshold": 1}'
        
        # Init and save keys
        curl -s -X POST -d "$INIT_PAYLOAD" "$VAULT_ADDR/v1/sys/init" | tee "$KEYS_FILE"
        echo -e "\n[Unsealer] Initialization complete. Keys saved to $KEYS_FILE"
        
        # Force a fresh status check immediately after init
        sleep 1
        HTTP_RESPONSE=$(curl -s "$VAULT_ADDR/v1/sys/health")
    fi

    # 3. Seal Status Check
    SEALED_STATUS=$(echo "$HTTP_RESPONSE" | jq -r '.sealed')

    if [ "$SEALED_STATUS" = "true" ]; then
        echo "[Unsealer] Vault is SEALED. Attempting unseal..."
        
        if [ ! -f "$KEYS_FILE" ]; then
            echo "[Unsealer] ERROR: $KEYS_FILE not found! Cannot unseal."
        else
            # Extract key using jq
            UNSEAL_KEY=$(jq -r '.keys_base64[0]' "$KEYS_FILE")
            
            # Unseal request
            UNSEAL_RESPONSE=$(curl -s -X POST -d "{\"key\": \"$UNSEAL_KEY\"}" "$VAULT_ADDR/v1/sys/unseal")
            
            # Check if it worked
            NEW_SEAL_STATUS=$(echo "$UNSEAL_RESPONSE" | jq -r '.sealed')
            if [ "$NEW_SEAL_STATUS" = "false" ]; then
                 echo "[Unsealer] SUCCESS: Vault is now Unsealed."
            else
                 echo "[Unsealer] FAILED: Vault is still sealed. Response: $UNSEAL_RESPONSE"
            fi
        fi
    else
        # Optional: Print a heartbeat or keep silent. 
        # Printing the date helps you know the container isn't frozen.
        echo "[Unsealer] $(date +'%H:%M:%S') - Vault is Healthy (Initialized & Unsealed)."
    fi

    # 4. Sleep
    sleep 120

done
