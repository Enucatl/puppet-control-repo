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
    local certname="${1:?Usage: create_vault_token <certname>}"
    : "${VAULT_TOKEN:?VAULT_TOKEN is not set}"
    : "${VAULT_ADDR:?VAULT_ADDR is not set}"

    local payload response
    local tls_args=()
    if [ -n "${VAULT_CACERT:-}" ]; then
        tls_args=(--cacert "$VAULT_CACERT")
    fi
    payload=$(jq -n --arg certname "$certname" '{
        policies: ["puppet-enrollment"], type: "service", ttl: "2h",
        num_uses: 1, renewable: false, no_default_policy: true,
        meta: {certname: $certname}
    }') || return 1
    if ! response=$(curl --fail --silent --show-error "${tls_args[@]}" \
        --header "X-Vault-Token: $VAULT_TOKEN" \
        --header 'Content-Type: application/json' \
        --request POST \
        --data "$payload" \
        "$VAULT_ADDR/v1/auth/token/create-orphan"); then
        echo 'Error: Failed to generate Vault enrollment token.' >&2
        return 1
    fi
    if ! VM_TOKEN=$(jq -er --arg certname "$certname" '
        .auth | select(
            .policies == ["puppet-enrollment"] and
            .metadata.certname == $certname and
            .token_type == "service" and .orphan == true and
            .renewable == false and .num_uses == 1 and
            (.lease_duration | type == "number" and . > 0 and . <= 7200)
        ) | .client_token | select(type == "string" and length > 0)
    ' <<<"$response"); then
        echo 'Error: Invalid Vault enrollment token response.' >&2
        return 1
    fi
    export VM_TOKEN
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
