#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/config.sh"
source "$SCRIPT_DIR/lib.sh"

# --- CLI ARGUMENTS ---
LETTER=""
VMID=""
CORES=8
MEMORY=16384
DISK_SIZE="50G"
NODE_TYPE="docker"

usage() {
    echo "Usage: $0 [-l LETTER] [-n NAME] [-i VMID] [-c CORES] [-m MEMORY_MB] [-d DISK_SIZE]"
    exit 1
}

while getopts "l:n:i:c:m:d:h" opt; do
    case $opt in
        l) LETTER="$OPTARG" ;;
        n) NEW_VM_NAME="$OPTARG" ;;
        i) VMID="$OPTARG" ;;
        c) CORES="$OPTARG" ;;
        m) MEMORY="$OPTARG" ;;
        d) DISK_SIZE="$OPTARG" ;;
        h) usage ;;
        *) usage ;;
    esac
done

if [ -z "${NEW_VM_NAME:-}" ]; then
    NEW_VM_NAME=$(pick_vm_name "$LETTER")
fi

# --- LOAD ENVIRONMENT ---
load_env

: "${IPV6_PREFIX:?IPV6_PREFIX is not set}"
: "${VAULT_TOKEN:?VAULT_TOKEN is not set}"
: "${VAULT_ADDR:?VAULT_ADDR is not set}"
: "${LOG_DIR:?LOG_DIR is not set}"

VM_LETTER="${NEW_VM_NAME:0:1}"
IPV6_POOL="${IPV6_PREFIX}:${VM_LETTER}000::/56"
IPV6_FIXED="${IPV6_PREFIX}:${VM_LETTER}100::/64"
export IPV6_POOL IPV6_FIXED PUPPET_SERVER DOMAIN_SUFFIX NODE_TYPE

# --- DEFAULTS ---
if [ -z "$VMID" ]; then
    VMID=$(pvesh get /cluster/nextid)
fi

STORAGE="$DEFAULT_STORAGE"
TEMPLATE_SRC="$SCRIPT_DIR/docker-cloud-init.yml.tmpl"
GENERATED_YAML="/var/lib/vz/snippets/generated-docker-cloud-init-$VMID.yml"

mkdir -p "$LOG_DIR"

echo "--- Starting Deployment for VM $VMID ($NEW_VM_NAME) ---"

# 1. Generate Vault token and inject all variables into the template
echo "[1/6] Generating Vault Token and Cloud-Init from template..."
create_vault_token
envsubst '${IPV6_POOL}${IPV6_FIXED}${VM_TOKEN}${VAULT_ADDR}${PUPPET_SERVER}${DOMAIN_SUFFIX}${NODE_TYPE}' < "$TEMPLATE_SRC" > "$GENERATED_YAML"

# 2. Pre-flight Cleanup
if qm status "$VMID" >/dev/null 2>&1; then
    echo "[2/6] Destroying existing VM $VMID..."
    qm stop "$VMID" >/dev/null 2>&1 || true
    qm destroy "$VMID" --purge
fi

# 3. Clone the Template
echo "[3/6] Cloning template..."
qm clone "$TEMPLATE_ID" "$VMID" --name "$NEW_VM_NAME" --storage "$STORAGE" --full 1

cleanup() {
    echo "Error detected. Cleaning up..."
    rm -f "$GENERATED_YAML" 2>/dev/null || true
    qm stop "$VMID" 2>/dev/null || true
    qm destroy "$VMID" --purge 2>/dev/null || true
}
trap cleanup ERR INT TERM

qm set "$VMID" \
    --cores "$CORES" \
    --memory "$MEMORY" \
    --agent 1 \
    --cpu host \
    --bios ovmf \
    --machine q35 \
    --efidisk0 "${STORAGE}:0,efitype=4m,pre-enrolled-keys=0" \
    --scsihw virtio-scsi-single
qm resize "$VMID" scsi0 "$DISK_SIZE"

# 4. Apply Configuration
echo "[4/6] Applying Cloud-Init..."
qm set "$VMID" \
    --cicustom "vendor=${SNIPPET_STORAGE}:snippets/$(basename "$GENERATED_YAML")" \
    --ipconfig0 "ip=dhcp" \
    --ciuser user \
    --sshkey ~/.ssh/id_ed25519.pub

# 5. Start and Wait for Logs
echo "[5/6] Starting VM..."
qm start "$VMID"

wait_for_cloudinit "$VMID"

# 6. Fetch Log and Cleanup
echo "[6/6] Fetching log to $LOG_DIR/cloud-init-output.log..."
qm guest exec "$VMID" -- cat /var/log/cloud-init-output.log | jq -r '."out-data"' >> "$LOG_DIR/cloud-init-output.log"

rm "$GENERATED_YAML"
trap - ERR INT TERM

echo "Removing Cloud-Init drive..."
qm set "$VMID" --delete ide2

echo "--- Deployment Complete! ---"
