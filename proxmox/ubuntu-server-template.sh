#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/config.sh"
source "$SCRIPT_DIR/lib.sh"
[[ ! -f .env ]] || load_env
REBUILD=false
while getopts 'i:rh' opt; do
    case $opt in
        i) TEMPLATE_ID="$OPTARG" ;;
        r) REBUILD=true ;;
        h) echo "Usage: $0 [-i TEMPLATE_ID] [-r]"; exit 0 ;;
        *) exit 1 ;;
    esac
done
VMID="$TEMPLATE_ID"
VMNAME=ubuntu-server-2604-template
OWNER=openvox-provisioning:template:ubuntu-26.04
export TARGET_IS_TEMPLATE=1
validate_target "$VMID" "$VMNAME" "$OWNER" "$REBUILD"
check_provisioning_host
STORAGE="$DEFAULT_STORAGE"
LOG_DIR="${LOG_DIR:-/var/log/openvox-provisioning}"
mkdir -p "$LOG_DIR"
IMAGE_NAME=ubuntu-26.04-server-cloudimg-amd64.img
IMAGE_URL="https://cloud-images.ubuntu.com/releases/resolute/release-20260927/$IMAGE_NAME"
IMAGE_SHA256=8800651811af9a85465ad1d552add729947bb16488dddb4a9b5305a3d97332b2
IMAGE_PATH="/var/lib/vz/template/iso/resolute-20260927-$IMAGE_NAME"
TEMPLATE_SRC="$SCRIPT_DIR/ubuntu-cloud-init.yml.tmpl"
SNIPPET_PATH="/var/lib/vz/snippets/generated-ubuntu-cloud-init-$VMID.yml"
CREATED=false
cleanup() {
    local status=$?
    trap - EXIT
    rm -f "$SNIPPET_PATH" "$IMAGE_PATH.part"
    if [[ $status != 0 && $CREATED == true ]]; then
        remove_target "$VMID" || echo 'Retaining builder: cleanup ownership check failed.' >&2
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

SSH_PUBLIC_KEY_JSON=$(jq -cn --arg key "$(cat ~/.ssh/id_ed25519.pub)" '$key')
export SSH_PUBLIC_KEY_JSON
INSTALL_OPENVOX_SCRIPT=$(sed 's/^/      /' "$SCRIPT_DIR/install-openvox.sh")
export INSTALL_OPENVOX_SCRIPT
envsubst '${INSTALL_OPENVOX_SCRIPT}${SSH_PUBLIC_KEY_JSON}' < "$TEMPLATE_SRC" > "$SNIPPET_PATH"

# 2. Download a dated image and verify even cached images before mutation.
if [[ ! -f $IMAGE_PATH ]]; then
    curl --fail --show-error --location --retry 3 "$IMAGE_URL" --output "$IMAGE_PATH.part"
    printf '%s  %s\n' "$IMAGE_SHA256" "$IMAGE_PATH.part" | sha256sum --check -
    mv "$IMAGE_PATH.part" "$IMAGE_PATH"
fi
printf '%s  %s\n' "$IMAGE_SHA256" "$IMAGE_PATH" | sha256sum --check -
if [[ $TARGET_EXISTS == true ]]; then remove_target "$VMID"; fi
qm create "$VMID" --name "$VMNAME" --description "$OWNER" --memory 2048 --cores 2 \
    --net0 virtio,bridge=vmbr0 --ipconfig0 ip=dhcp --searchdomain "$DOMAIN_SUFFIX"
CREATED=true
qm importdisk "$VMID" "$IMAGE_PATH" "$STORAGE"
qm set "$VMID" --scsihw virtio-scsi-pci --scsi0 "$STORAGE:vm-$VMID-disk-0,discard=on,ssd=1"
qm resize "$VMID" scsi0 10G
qm set "$VMID" --ide2 "$STORAGE:cloudinit" --boot order=scsi0 --serial0 socket --vga serial0
qm set "$VMID" --cicustom "vendor=$SNIPPET_STORAGE:snippets/$(basename "$SNIPPET_PATH")" \
    --ciuser user_l --sshkey ~/.ssh/id_ed25519.pub --agent enabled=1,fstrim_cloned_disks=1
qm start "$VMID"
wait_for_cloudinit "$VMID"
guest_exec "$VMID" cat /var/log/cloud-init-output.log >> "$LOG_DIR/cloud-init-output.log"
guest_exec "$VMID" bash -c '
    set -e
    . /etc/os-release
    test "$ID:$VERSION_ID" = ubuntu:26.04
    dpkg-query -W openvox-agent cloud-init qemu-guest-agent
    /opt/puppetlabs/puppet/bin/ruby -ropenssl -e "puts OpenSSL::OPENSSL_VERSION"
    test ! -d /etc/puppetlabs/puppet/ssl/private_keys
    test ! -e /etc/puppetlabs/puppet/csr_attributes.yaml
' > "$LOG_DIR/template-$VMID-packages.txt"
jq -n --arg url "$IMAGE_URL" --arg checksum "$IMAGE_SHA256" \
    --arg revision "${CONTROL_REVISION:-$(git -C "$SCRIPT_DIR" rev-parse HEAD)}" --argjson vmid "$VMID" \
    '{vmid:$vmid, image_url:$url, image_sha256:$checksum, control_revision:$revision,
      openvox_release:"resolute-28001", openvox_agent:"9.1.1+homelab.28001-1+ubuntu26.04"}' \
    > "$LOG_DIR/template-$VMID-provenance.json"
sha256sum "$SCRIPT_DIR"/*.sh "$SCRIPT_DIR"/*.tmpl > "$LOG_DIR/template-$VMID-sources.sha256"
guest_exec "$VMID" bash -c '
    set -e
    cloud-init clean --logs --seed --machine-id
    rm -f /etc/ssh/ssh_host_* /var/lib/dhcp/*
    rm -f /var/lib/dbus/machine-id
    ln -s /etc/machine-id /var/lib/dbus/machine-id
    sync
'
qm stop "$VMID"
qm set "$VMID" --delete cicustom
qm template "$VMID"
echo "Ubuntu 26.04 template ready at $VMID; provenance in $LOG_DIR."
