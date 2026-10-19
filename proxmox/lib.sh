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

# Check the cluster before writing credentials or touching a selected guest.
validate_target() {
    local vmid="$1" name="$2" owner="$3" rebuild="$4" resources guest config description encoded_owner
    [[ $vmid =~ ^[1-9][0-9]{2,8}$ && $vmid != 9000 ]] || {
        echo 'Invalid or protected VM ID (9000 is reserved).' >&2; return 1;
    }
    [[ ${TARGET_IS_TEMPLATE:-0} == 1 || $vmid != "$TEMPLATE_ID" ]] || {
        echo 'Target must differ from the source template.' >&2; return 1;
    }
    resources=$(pvesh get /cluster/resources --type vm --output-format json) || return 1
    jq -e --argjson id "$vmid" --arg name "$name" '
        type == "array" and all(.[]; (.vmid | type) == "number") and
        all(.[]; .name != $name or .vmid == $id)
    ' <<<"$resources" >/dev/null || {
        echo 'Invalid cluster inventory or hostname already allocated.' >&2; return 1;
    }
    guest=$(jq -ce --argjson id "$vmid" '[.[] | select(.vmid == $id)]' <<<"$resources") || return 1
    TARGET_EXISTS=false
    if [[ $(jq length <<<"$guest") != 0 ]]; then
        [[ $(jq length <<<"$guest") == 1 && $rebuild == true ]] || {
            echo 'Existing target requires an explicit rebuild.' >&2; return 1;
        }
        jq -e --arg name "$name" --arg node "$(hostname -s)" \
            '.[0] | .type == "qemu" and .node == $node and .name == $name' <<<"$guest" >/dev/null || {
            echo 'Target is not the designated local VM.' >&2; return 1;
        }
        config=$(qm config "$vmid") || return 1
        description=$(sed -n 's/^description: //p' <<<"$config")
        encoded_owner=$(jq -rn --arg owner "$owner" '$owner | @uri') || return 1
        [[ $(sed -n 's/^name: //p' <<<"$config") == "$name" &&
           ( $description == "$owner" || $description == "$encoded_owner" ) ]] || {
            echo 'Target ownership marker/name does not match.' >&2; return 1;
        }
        if [[ ${TARGET_IS_TEMPLATE:-0} != 1 ]] && grep -q '^template: 1$' <<<"$config"; then
            echo 'Refusing to replace a template with a guest.' >&2; return 1
        fi
        TARGET_EXISTS=true
    fi
    TARGET_NAME="$name"
    TARGET_OWNER="$owner"
}

remove_target() {
    local vmid="$1"
    validate_target "$vmid" "$TARGET_NAME" "$TARGET_OWNER" true || return 1
    if [[ $TARGET_EXISTS == true ]]; then
        qm stop "$vmid" || return 1
        qm destroy "$vmid" --purge
    fi
}

check_provisioning_host() {
    local tool storage
    [[ $(id -u) == 0 ]] || { echo 'Run on the Proxmox host as root.' >&2; return 1; }
    for tool in qm pvesh jq curl envsubst sha256sum ip; do
        command -v "$tool" >/dev/null || { echo "Missing prerequisite: $tool" >&2; return 1; }
    done
    test -s ~/.ssh/id_ed25519.pub || { echo 'Missing root SSH public key.' >&2; return 1; }
    ip link show vmbr0 >/dev/null || return 1
    storage=$(pvesh get "/storage/$SNIPPET_STORAGE" --output-format json) || return 1
    jq -e '.path == "/var/lib/vz" and (.content | split(",") | index("snippets") != null)' \
        <<<"$storage" >/dev/null || { echo 'Snippet storage must support snippets at /var/lib/vz.' >&2; return 1; }
    storage=$(pvesh get "/storage/$DEFAULT_STORAGE" --output-format json) || return 1
    jq -e '.content | split(",") | index("images") != null' <<<"$storage" >/dev/null || return 1
    test -d /var/lib/vz/snippets && test -d /var/lib/vz/template/iso
}

guest_exec() {
    local result
    result=$(qm guest exec "$1" -- "${@:2}") || return 1
    jq -e '.exitcode == 0' <<<"$result" >/dev/null || {
        jq -r '."err-data" // ."out-data" // "Guest command failed"' <<<"$result" >&2
        return 1
    }
    jq -r '."out-data" // empty' <<<"$result"
}

verify_guest_identity() {
    [[ $(guest_exec "$1" hostname) == "$2" && $(guest_exec "$1" hostname -f) == "$3" ]] || {
        echo 'Guest hostname/FQDN does not match the requested identity.' >&2; return 1;
    }
}

reboot_and_verify_guest() {
    local vmid="$1" previous current deadline=$((SECONDS + 300))
    previous=$(guest_exec "$vmid" cat /proc/sys/kernel/random/boot_id) || return 1
    qm reboot "$vmid" || return 1
    while (( SECONDS < deadline )); do
        current=$(guest_exec "$vmid" cat /proc/sys/kernel/random/boot_id 2>/dev/null) || current=""
        if [[ -n $current && $current != "$previous" ]]; then
            wait_for_cloudinit "$vmid" || return 1
            verify_guest_identity "$@"
            return
        fi
        sleep 5
    done
    echo 'Timed out waiting for the guest reboot.' >&2
    return 1
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
    local deadline=$((SECONDS + 1800))
    while (( SECONDS < deadline )); do
        STATUS=$(qm guest exec "$vmid" -- cloud-init status 2>/dev/null | jq -r '."out-data"' || echo "unknown")

        if [[ "$STATUS" == *"status: done"* ]]; then
            echo -e "\n[Success] Cloud-Init reports done!"
            return
        elif [[ "$STATUS" == *"status: error"* ]]; then
            echo -e "\n[Error] Cloud-Init failed!"
            echo "--- cloud-init status detail ---"
            qm guest exec "$vmid" -- cloud-init status --long 2>/dev/null | jq -r '."out-data" // ."err-data" // .'
            echo "--- journalctl cloud-init (last 80 lines) ---"
            qm guest exec "$vmid" -- bash -c "journalctl -u cloud-init --no-pager -n 80 2>&1" \
                | jq -r '."out-data" // ."err-data" // .'
            return 1
        fi

        echo -n "."
        sleep 10
    done
    echo 'Timed out waiting for cloud-init/QEMU guest agent.' >&2
    return 1
}
