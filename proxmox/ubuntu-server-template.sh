#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/config.sh"
source "$SCRIPT_DIR/lib.sh"

# --- LOAD ENVIRONMENT VARIABLES ---
load_env

: "${FORGE_KEY:?FORGE_KEY is not set}"
: "${LOG_DIR:?LOG_DIR is not set}"

# --- CONFIGURATION VARIABLES ---
VMID=9000
VMNAME="ubuntu-server-2404-template"
STORAGE="$DEFAULT_STORAGE"
IMAGE_NAME="noble-server-cloudimg-amd64.img"
IMAGE_URL="https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"

# Paths
TEMPLATE_SRC="$SCRIPT_DIR/ubuntu-cloud-init.yml.tmpl"
SNIPPET_PATH="/var/lib/vz/snippets/generated-ubuntu-cloud-init.yml"

echo "--- Starting Template Build for VM $VMID ---"

# --- 1. PREPARE CLOUD-INIT SNIPPET ---
echo "Generating Cloud-Init snippet from template..."
export SSH_PUBLIC_KEY
SSH_PUBLIC_KEY=$(cat ~/.ssh/id_ed25519.pub)
envsubst '${FORGE_KEY}${SSH_PUBLIC_KEY}' < "$TEMPLATE_SRC" > "$SNIPPET_PATH"

# 2. Download the Image (if not already present)
if [ ! -f "/var/lib/vz/template/iso/$IMAGE_NAME" ]; then
    echo "Downloading Ubuntu Cloud Image..."
    wget -O /var/lib/vz/template/iso/$IMAGE_NAME $IMAGE_URL
fi

# 3. Destroy old VM if it exists
if qm status $VMID &>/dev/null; then
    echo "VM $VMID exists. Destroying..."
    qm stop $VMID &>/dev/null || true
    qm destroy $VMID --purge
fi

# 4. Create VM Shell
qm create $VMID --name $VMNAME --memory 2048 --cores 2 --net0 virtio,bridge=vmbr0 --ipconfig0 ip=dhcp

# 5. Import and Attach Disk
qm importdisk $VMID /var/lib/vz/template/iso/$IMAGE_NAME $STORAGE
qm set $VMID --scsihw virtio-scsi-pci --scsi0 $STORAGE:vm-$VMID-disk-0,discard=on,ssd=1
qm resize $VMID scsi0 10G

# 6. Hardware Config
qm set $VMID --ide2 $STORAGE:cloudinit
qm set $VMID --boot c --bootdisk scsi0
qm set $VMID --serial0 socket --vga serial0

# 7. Apply Cloud-Init (Using the generated file)
qm set $VMID --cicustom "vendor=$SNIPPET_STORAGE:snippets/$(basename $SNIPPET_PATH)"
qm set $VMID --ciuser user_l
qm set $VMID --sshkey ~/.ssh/id_ed25519.pub
qm set $VMID --agent enabled=1,fstrim_cloned_disks=1

# 8. Start and Wait
echo "Starting VM builder..."
qm start $VMID

wait_for_cloudinit $VMID

qm guest exec $VMID -- cat /var/log/cloud-init-output.log | jq -r '."out-data"' >> "$LOG_DIR/cloud-init-output.log"

echo "Cleaning up VM for templating..."
qm guest exec $VMID -- cloud-init clean --logs
qm guest exec $VMID -- bash -c 'rm /etc/ssh/ssh_host_*'
qm guest exec $VMID -- bash -c 'truncate -s 0 /etc/machine-id'
qm guest exec $VMID -- bash -c 'rm -f /var/lib/dbus/machine-id && ln -s /etc/machine-id /var/lib/dbus/machine-id'
qm guest exec $VMID -- bash -c 'rm -f /var/lib/dhcp/*'

qm stop $VMID
echo "VM has shut down."

rm "$SNIPPET_PATH"

# 9. Cleanup and Convert
qm set $VMID --delete cicustom

echo "Converting to Template..."
qm template $VMID

echo "--- SUCCESS! ---"
