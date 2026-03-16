#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/config.sh"
source "$SCRIPT_DIR/lib.sh"

# --- CLI ARGUMENTS ---
LETTER=""
VMID=""
CORES=8
MEMORY=8000
DISK_SIZE="128G"

usage() {
    echo "Usage: $0 [-l LETTER] [-i VMID] [-c CORES] [-m MEMORY_MB] [-d DISK_SIZE]"
    exit 1
}

while getopts "l:i:c:m:d:h" opt; do
    case $opt in
        l) LETTER="$OPTARG" ;;
        i) VMID="$OPTARG" ;;
        c) CORES="$OPTARG" ;;
        m) MEMORY="$OPTARG" ;;
        d) DISK_SIZE="$OPTARG" ;;
        h) usage ;;
        *) usage ;;
    esac
done

# --- LOAD ENVIRONMENT ---
load_env

: "${VAULT_TOKEN:?VAULT_TOKEN is not set}"
: "${VAULT_ADDR:?VAULT_ADDR is not set}"

# --- DEFAULTS ---
if [ -z "$VMID" ]; then
    VMID=$(pvesh get /cluster/nextid)
fi

NEW_VM_NAME=$(pick_vm_name "$LETTER")

STORAGE="$DEFAULT_STORAGE"

# Paths
TEMPLATE_SRC="$SCRIPT_DIR/desktop-cloud-init.yml.tmpl"
GENERATED_YAML="/var/lib/vz/snippets/generated-desktop-$VMID.yml"

# Hardware
PCI_GPU="0000:09:00.0"
PCI_AUDIO="0000:09:00.1"

echo "--- Preparing Deployment for $NEW_VM_NAME (VMID: $VMID) ---"

# 1. Generate Short-Lived Token for the VM
echo "[1/5] Generating Vault Token for Autosigning..."
create_vault_token

# 2. Inject Token into Cloud-Init using envsubst
echo "[2/5] Injecting variables into Cloud-Init..."
export PUPPET_SERVER CMK_DOMAIN CMK_SITE CMK_REG_USER DOMAIN_SUFFIX
envsubst '${VM_TOKEN}${VAULT_ADDR}${PUPPET_SERVER}${CMK_DOMAIN}${CMK_SITE}${CMK_REG_USER}${DOMAIN_SUFFIX}' < "$TEMPLATE_SRC" > "$GENERATED_YAML"

# 3. VM Cleanup
if qm status "$VMID" >/dev/null 2>&1; then
    echo "[3/5] Cleaning up old VM..."
    qm stop "$VMID" >/dev/null 2>&1 || true
    qm destroy "$VMID" --purge
fi

# 4. Clone & Config
echo "[4/5] Cloning and Configuring..."
qm clone "$TEMPLATE_ID" "$VMID" --name "$NEW_VM_NAME" --storage "$STORAGE" --full 1

cleanup() {
    echo "Error detected. Cleaning up..."
    rm -f "$GENERATED_YAML" 2>/dev/null || true
    qm stop "$VMID" 2>/dev/null || true
    qm destroy "$VMID" --purge 2>/dev/null || true
}
trap cleanup ERR INT TERM

qm resize "$VMID" scsi0 "$DISK_SIZE"

# Desktop Performance
qm set "$VMID" --agent 1 --cores "$CORES" --memory "$MEMORY" --cpu host
qm set "$VMID" --machine q35 --bios ovmf
qm set "$VMID" --efidisk0 "$STORAGE:0,efitype=4m,pre-enrolled-keys=1"

# Apply Generated Cloud-Init
qm set "$VMID" \
    --cicustom "vendor=${SNIPPET_STORAGE}:snippets/$(basename "$GENERATED_YAML")" \
    --ipconfig0 "ip=dhcp"

# 5. Start
echo "[5/5] Starting VM..."
qm start "$VMID"

echo "--- Deployment Running ---"

wait_for_cloudinit "$VMID"

# 6. Fetch Log and Finalize
echo "[6/6] Cloud-Init complete. Fetching log..."
qm guest exec "$VMID" -- cat /var/log/cloud-init-output.log | jq -r '."out-data"' >> "$LOG_DIR/cloud-init-output.log"

rm "$GENERATED_YAML"
trap - ERR INT TERM

echo "Rebooting VM to finalize desktop environment..."
qm reboot "$VMID"

echo "VM Name: $NEW_VM_NAME"
