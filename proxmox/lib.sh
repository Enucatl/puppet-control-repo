#!/bin/bash
# Shared functions for Proxmox provisioning scripts

pick_vm_name() {
    local letter="${1:-}"
    if [ -n "$letter" ]; then
        grep -i "^${letter}" /usr/share/dict/words | grep -E '^[a-zA-Z]+$' | shuf -n 1 | tr '[:upper:]' '[:lower:]'
    else
        grep -E '^[a-zA-Z]+$' /usr/share/dict/words | shuf -n 1 | tr '[:upper:]' '[:lower:]'
    fi
}

load_env() {
    local env_file="${1:-.env}"
    if [ -f "$env_file" ]; then
        export $(grep -v '^#' "$env_file" | xargs)
    else
        echo "Error: $env_file file not found!"
        exit 1
    fi
}

create_vault_token() {
    : "${VAULT_TOKEN:?VAULT_TOKEN is not set}"
    : "${VAULT_ADDR:?VAULT_ADDR is not set}"

    export VM_TOKEN
    VM_TOKEN=$(curl --insecure -s --header "X-Vault-Token: $VAULT_TOKEN" \
        --request POST \
        --data '{"policies": ["puppet"], "ttl": "2h", "renewable": true}' \
        "$VAULT_ADDR/v1/auth/token/create" | jq -r .auth.client_token)

    if [ -z "$VM_TOKEN" ] || [ "$VM_TOKEN" == "null" ]; then
        echo "Error: Failed to generate Vault Token."
        exit 1
    fi
}

wait_for_cloudinit() {
    local vmid="$1"
    echo "Waiting for Cloud-Init to finish..."
    while true; do
        STATUS=$(qm guest exec "$vmid" -- cloud-init status 2>/dev/null | jq -r '."out-data"' || echo "unknown")

        if [[ "$STATUS" == *"status: done"* ]]; then
            echo -e "\n[Success] Cloud-Init reports done!"
            break
        elif [[ "$STATUS" == *"status: error"* ]]; then
            echo -e "\n[Error] Cloud-Init failed!"
            echo "--- cloud-init status detail ---"
            qm guest exec "$vmid" -- cloud-init status --long 2>/dev/null | jq -r '."out-data" // ."err-data" // .'
            echo "--- journalctl cloud-init (last 80 lines) ---"
            qm guest exec "$vmid" -- bash -c "journalctl -u cloud-init --no-pager -n 80 2>&1" \
                | jq -r '."out-data" // ."err-data" // .'
            exit 1
        fi

        echo -n "."
        sleep 10
    done
}
