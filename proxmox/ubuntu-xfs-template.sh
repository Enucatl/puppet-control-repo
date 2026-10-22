#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/config.sh"
source "$SCRIPT_DIR/lib.sh"
[[ ! -f .env ]] || load_env
XFS_TEMPLATE_ID=9002
BUILDER_ID=9012
while getopts 'i:b:h' opt; do
    case $opt in
        i) XFS_TEMPLATE_ID="$OPTARG" ;;
        b) BUILDER_ID="$OPTARG" ;;
        h) echo "Usage: $0 [-i XFS_TEMPLATE_ID] [-b BUILDER_ID]"; exit 0 ;;
        *) exit 1 ;;
    esac
done
[[ $XFS_TEMPLATE_ID != "$BUILDER_ID" && $XFS_TEMPLATE_ID != "$TEMPLATE_ID" && $BUILDER_ID != "$TEMPLATE_ID" ]] || {
    echo 'Builder, XFS template and existing template IDs must differ.' >&2; exit 1;
}
export TARGET_IS_TEMPLATE=1
validate_target "$XFS_TEMPLATE_ID" ubuntu-server-2604-xfs-template openvox-provisioning:template:ubuntu-26.04-xfs false
validate_target "$BUILDER_ID" ubuntu-xfs-builder openvox-provisioning:builder:ubuntu-26.04-xfs false
check_provisioning_host
LOG_DIR="${LOG_DIR:-/var/log/openvox-provisioning}"
mkdir -p "$LOG_DIR"
RELEASE_URL=https://cloud-images.ubuntu.com/releases/resolute/release-20260927
IMAGE_NAME=ubuntu-26.04-server-cloudimg-amd64.img
IMAGE_SHA256=8800651811af9a85465ad1d552add729947bb16488dddb4a9b5305a3d97332b2
ROOT_NAME=ubuntu-26.04-server-cloudimg-amd64-root.tar.xz
# Independently authenticated against Canonical's signed SHA256SUMS (see README).
ROOT_SHA256=ccd08a7a76cdc69034dd2682b4913bdd051cb71ad87d0b66689015aa068e61c8
# Ubuntu 26.04 no longer publishes Curtin debs; run Canonical's pinned source.
CURTIN_REVISION=93c36fb6a0271fefd2bf311418b95eb48f856d5f
CURTIN_SHA256=a98344999b7aab9b51a6d68f6030a857e99b97a4f61b3e354e626d60d3bd92c9
IMAGE_PATH="/var/lib/vz/template/iso/resolute-20260927-$IMAGE_NAME"
SNIPPET_PATH="/var/lib/vz/snippets/generated-xfs-builder-$BUILDER_ID.yml"
TARGET_SNIPPET="/var/lib/vz/snippets/generated-xfs-template-$XFS_TEMPLATE_ID.yml"
cleanup() {
    local status=$?
    trap - EXIT
    rm -f "$IMAGE_PATH.part"
    if [[ $status != 0 ]]; then
        echo "Retaining builder $BUILDER_ID, target $XFS_TEMPLATE_ID and snippets for diagnosis; no existing VM is deleted." >&2
    else
        rm -f "$SNIPPET_PATH" "$TARGET_SNIPPET"
    fi
    exit "$status"
}
trap cleanup EXIT
if [[ ! -f $IMAGE_PATH ]]; then
    curl --fail --show-error --location --retry 3 "$RELEASE_URL/$IMAGE_NAME" --output "$IMAGE_PATH.part"
    printf '%s  %s\n' "$IMAGE_SHA256" "$IMAGE_PATH.part" | sha256sum --check -
    mv "$IMAGE_PATH.part" "$IMAGE_PATH"
fi
printf '%s  %s\n' "$IMAGE_SHA256" "$IMAGE_PATH" | sha256sum --check -
SSH_PUBLIC_KEY_JSON=$(jq -cn --arg key "$(cat ~/.ssh/id_ed25519.pub)" '$key')
export SSH_PUBLIC_KEY_JSON
{
    cat <<YAML
#cloud-config
users:
  - name: user_l
    groups: sudo
    shell: /bin/bash
    sudo: ['ALL=(ALL) NOPASSWD:ALL']
    ssh_authorized_keys: [$SSH_PUBLIC_KEY_JSON]
packages: [xfsprogs, dosfstools, qemu-guest-agent, gdisk, parted, file, rsync, wget, efibootmgr, lvm2, mdadm, btrfs-progs, bcache-tools, open-iscsi, python3-apt, python3-yaml, python3-attr, python3-oauthlib, python3-pyudev, python3-jsonschema, python3-packaging, python3-debian]
write_files:
  - path: /var/tmp/xfs-curtin.yml
    content: |
YAML
    sed 's/^/      /' "$SCRIPT_DIR/ubuntu-xfs-curtin.yml"
    cat <<YAML
runcmd:
  - systemctl enable --now qemu-guest-agent
  - |
    set -eu
    test -d /sys/firmware/efi
    targets=\$(lsblk -dn -o PATH,SERIAL | awk '\$2 == "openvox-xfs-target" {print \$1}')
    test "\$(printf '%s\n' "\$targets" | wc -l)" = 1
    target=\$targets
    test -b "\$target"
    test "\$(lsblk -dn -o SERIAL "\$target")" = openvox-xfs-target
    test "\$(blockdev --getsize64 "\$target")" = 12884901888
    test "\$(lsblk -nr -o NAME "\$target" | wc -l)" = 1
    test -z "\$(lsblk -nr -o MOUNTPOINTS "\$target" | tr -d '[:space:]')"
    test "\$target" != "/dev/\$(lsblk -no PKNAME "\$(findmnt -n -o SOURCE /)")"
    curl --fail --show-error --location --retry 3 '$RELEASE_URL/$ROOT_NAME' --output /var/tmp/ubuntu-root.tar.xz
    printf '%s  %s\n' '$ROOT_SHA256' /var/tmp/ubuntu-root.tar.xz | sha256sum --check -
    sed -i "s|__TARGET_DISK__|\$target|" /var/tmp/xfs-curtin.yml
    curl --fail --show-error --location --retry 3 'https://codeload.github.com/canonical/curtin/tar.gz/$CURTIN_REVISION' --output /var/tmp/curtin.tar.gz
    printf '%s  %s\n' '$CURTIN_SHA256' /var/tmp/curtin.tar.gz | sha256sum --check -
    mkdir -p /opt/curtin
    tar -xzf /var/tmp/curtin.tar.gz -C /opt/curtin --strip-components=1
    export PATH=/opt/curtin/bin:/opt/curtin/helpers:\$PATH
    export PYTHON=python3
    curtin --showtrace -v -c /var/tmp/xfs-curtin.yml install
YAML
} > "$SNIPPET_PATH"
qm create "$BUILDER_ID" --name ubuntu-xfs-builder --description openvox-provisioning:builder:ubuntu-26.04-xfs \
    --memory 2048 --cores 2 --bios ovmf --machine q35 --net0 virtio,bridge=vmbr0 \
    --ipconfig0 ip=dhcp --scsihw virtio-scsi-single --agent 1 --serial0 socket --vga serial0
qm importdisk "$BUILDER_ID" "$IMAGE_PATH" "$DEFAULT_STORAGE"
qm set "$BUILDER_ID" --scsi0 "$DEFAULT_STORAGE:vm-$BUILDER_ID-disk-0,discard=on,ssd=1,iothread=1"
qm resize "$BUILDER_ID" scsi0 10G
qm set "$BUILDER_ID" --scsi1 "$DEFAULT_STORAGE:12,serial=openvox-xfs-target,discard=on,ssd=1,iothread=1" \
    --ide2 "$DEFAULT_STORAGE:cloudinit" --efidisk0 "$DEFAULT_STORAGE:0,efitype=4m,pre-enrolled-keys=0" \
    --boot order=scsi0 --cicustom "vendor=$SNIPPET_STORAGE:snippets/$(basename "$SNIPPET_PATH")" \
    --ciuser user_l --sshkey ~/.ssh/id_ed25519.pub
qm start "$BUILDER_ID"
wait_for_cloudinit "$BUILDER_ID"
guest_exec "$BUILDER_ID" cat /var/log/curtin/install.log > "$LOG_DIR/xfs-curtin-$BUILDER_ID.log"
qm shutdown "$BUILDER_ID" --timeout 120
# Moving only scsi1 into a fresh VM proves the target needs neither builder root nor NVRAM.
qm create "$XFS_TEMPLATE_ID" --name ubuntu-server-2604-xfs-template \
    --description openvox-provisioning:template:ubuntu-26.04-xfs \
    --memory 2048 --cores 2 --bios ovmf --machine q35 --scsihw virtio-scsi-single \
    --net0 virtio,bridge=vmbr0 --agent 1 --serial0 socket --vga serial0
qm disk move "$BUILDER_ID" scsi1 --target-vmid "$XFS_TEMPLATE_ID" --target-disk scsi0
INSTALL_OPENVOX_SCRIPT=$(sed 's/^/      /' "$SCRIPT_DIR/install-openvox.sh")
export INSTALL_OPENVOX_SCRIPT
envsubst '${INSTALL_OPENVOX_SCRIPT}${SSH_PUBLIC_KEY_JSON}' < "$SCRIPT_DIR/ubuntu-cloud-init.yml.tmpl" > "$TARGET_SNIPPET"
qm set "$XFS_TEMPLATE_ID" --ide2 "$DEFAULT_STORAGE:cloudinit" \
    --efidisk0 "$DEFAULT_STORAGE:0,efitype=4m,pre-enrolled-keys=0" --boot order=scsi0 \
    --cicustom "vendor=$SNIPPET_STORAGE:snippets/$(basename "$TARGET_SNIPPET")" \
    --ipconfig0 ip=dhcp --searchdomain "$DOMAIN_SUFFIX" --ciuser user_l --sshkey ~/.ssh/id_ed25519.pub
qm start "$XFS_TEMPLATE_ID"
wait_for_cloudinit "$XFS_TEMPLATE_ID"
guest_exec "$XFS_TEMPLATE_ID" bash -c '
    set -eu
    test -d /sys/firmware/efi
    test "$(findmnt -n -o FSTYPE /)" = xfs
    xfs_info /
    findmnt -t xfs,ext4,vfat
    findmnt -n -o OPTIONS / | grep -qw noatime
    ! findmnt -n -o OPTIONS / | grep -qw discard
    systemctl is-enabled fstrim.timer
    xfs_info / | grep -q "reflink=1"
    xfs_info / | grep -q "ftype=1"
    test "$(findmnt -n -o FSTYPE /boot)" = ext4
    test "$(findmnt -n -o FSTYPE /boot/efi)" = vfat
    dpkg-query -W openvox-agent cloud-init qemu-guest-agent xfsprogs
    test ! -d /etc/puppetlabs/puppet/ssl/private_keys
    test ! -e /etc/puppetlabs/puppet/csr_attributes.yaml
' > "$LOG_DIR/xfs-template-$XFS_TEMPLATE_ID-checks.txt"
expected_short=$(guest_exec "$XFS_TEMPLATE_ID" hostname)
expected_fqdn=$(guest_exec "$XFS_TEMPLATE_ID" hostname -f)
reboot_and_verify_guest "$XFS_TEMPLATE_ID" "$expected_short" "$expected_fqdn"
jq -n --arg image "$RELEASE_URL/$IMAGE_NAME" --arg image_sha "$IMAGE_SHA256" \
    --arg root "$RELEASE_URL/$ROOT_NAME" --arg root_sha "$ROOT_SHA256" \
    --arg curtin_revision "$CURTIN_REVISION" --arg curtin_sha "$CURTIN_SHA256" \
    --arg revision "${CONTROL_REVISION:-$(git -C "$SCRIPT_DIR" rev-parse HEAD)}" \
    --argjson vmid "$XFS_TEMPLATE_ID" \
    '{vmid:$vmid,image_url:$image,image_sha256:$image_sha,root_url:$root,root_sha256:$root_sha,curtin_revision:$curtin_revision,curtin_sha256:$curtin_sha,control_revision:$revision}' \
    > "$LOG_DIR/xfs-template-$XFS_TEMPLATE_ID-provenance.json"
sha256sum "$SCRIPT_DIR/ubuntu-xfs-template.sh" "$SCRIPT_DIR/ubuntu-xfs-curtin.yml" \
    "$SCRIPT_DIR/ubuntu-cloud-init.yml.tmpl" "$SCRIPT_DIR/install-openvox.sh" > "$LOG_DIR/xfs-template-$XFS_TEMPLATE_ID-sources.sha256"
guest_exec "$XFS_TEMPLATE_ID" bash -c '
    set -e
    cloud-init clean --logs --seed --machine-id
    rm -f /etc/ssh/ssh_host_* /var/lib/dhcp/* /var/lib/dbus/machine-id
    ln -s /etc/machine-id /var/lib/dbus/machine-id
    sync
'
qm shutdown "$XFS_TEMPLATE_ID" --timeout 120
qm set "$XFS_TEMPLATE_ID" --delete cicustom
qm template "$XFS_TEMPLATE_ID"
echo "XFS template $XFS_TEMPLATE_ID ready; stopped builder $BUILDER_ID retained for review. Provenance in $LOG_DIR."
