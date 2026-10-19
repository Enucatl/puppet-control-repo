#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/config.sh"
source "$SCRIPT_DIR/lib.sh"
[[ ! -f .env ]] || load_env

LETTER=""
NEW_VM_NAME=""
VMID=""
CORES=8
MEMORY=16384
DISK_SIZE="50G"
NODE_TYPE="docker"
BASE_ONLY=false
REBUILD=false

usage() {
    echo "Usage: $0 [-b] [-r] [-n NAME | -l LETTER] [-i VMID] [-t TEMPLATE_ID] [-s PUPPET_SERVER] [-c CORES] [-m MEMORY_MB] [-d DISK_SIZE]"
    echo '  -b: infrastructure base without Vault/Puppet enrollment; -r: rebuild an owned development VM'
}
while getopts "brl:n:i:t:s:c:m:d:h" opt; do
    case $opt in
        b) BASE_ONLY=true ;;
        r) REBUILD=true ;;
        l) LETTER="$OPTARG" ;;
        n) NEW_VM_NAME="$OPTARG" ;;
        i) VMID="$OPTARG" ;;
        t) TEMPLATE_ID="$OPTARG" ;;
        s) PUPPET_SERVER="$OPTARG" ;;
        c) CORES="$OPTARG" ;;
        m) MEMORY="$OPTARG" ;;
        d) DISK_SIZE="$OPTARG" ;;
        h) usage; exit 0 ;;
        *) usage; exit 1 ;;
    esac
done
[[ $REBUILD != true || ( -n $VMID && -n $NEW_VM_NAME ) ]] || {
    echo 'Rebuild requires an explicit VM ID and name.' >&2; exit 1;
}
[[ -z $LETTER || $LETTER =~ ^[a-zA-Z]$ ]] || { echo 'Invalid initial letter.' >&2; exit 1; }
[[ -n $NEW_VM_NAME ]] || NEW_VM_NAME=$(pick_vm_name "$LETTER")
[[ ${#NEW_VM_NAME} -le 63 && $NEW_VM_NAME =~ ^[a-z]([a-z0-9-]*[a-z0-9])?$ ]] || {
    echo 'NAME must be a lowercase short DNS hostname.' >&2; exit 1;
}
[[ $CORES =~ ^[1-9][0-9]*$ && $MEMORY =~ ^[1-9][0-9]*$ && $DISK_SIZE =~ ^[1-9][0-9]*G$ ]] || {
    echo 'Invalid resources; use positive cores/MiB and an absolute disk size such as 50G.' >&2; exit 1;
}
[[ -n $VMID ]] || VMID=$(pvesh get /cluster/nextid)
VM_FQDN="${NEW_VM_NAME}.${DOMAIN_SUFFIX}"
OWNER="openvox-provisioning:docker:${VM_FQDN}"
validate_target "$VMID" "$NEW_VM_NAME" "$OWNER" "$REBUILD"
check_provisioning_host
qm config "$TEMPLATE_ID" | grep -q '^template: 1$' || { echo 'Source must be a template.' >&2; exit 1; }

LOG_DIR="${LOG_DIR:-/var/log/openvox-provisioning}"
STORAGE="$DEFAULT_STORAGE"
GENERATED_YAML="/var/lib/vz/snippets/generated-docker-cloud-init-$VMID.yml"
export NEW_VM_NAME VM_FQDN DOMAIN_SUFFIX
if [[ $BASE_ONLY == true ]]; then
    TEMPLATE_SRC="$SCRIPT_DIR/atlas-cloud-init.yml.tmpl"
    install -m 0600 /dev/null "$GENERATED_YAML"
    envsubst '${NEW_VM_NAME}${VM_FQDN}' < "$TEMPLATE_SRC" > "$GENERATED_YAML"
else
    : "${IPV6_PREFIX:?IPV6_PREFIX is not set}"
    VM_LETTER="${NEW_VM_NAME:0:1}"
    [[ $VM_LETTER =~ ^[a-f]$ && $IPV6_PREFIX =~ ^([[:xdigit:]]{1,4}:){2}[[:xdigit:]]{1,4}$ ]] || {
        echo 'Docker IPv6 requires an a-f hostname initial and a three-hextet /48 prefix.' >&2; exit 1;
    }
    IPV6_POOL="${IPV6_PREFIX}:${VM_LETTER}000::/56"
    IPV6_FIXED="${IPV6_PREFIX}:${VM_LETTER}100::/64"
    export IPV6_POOL IPV6_FIXED PUPPET_SERVER NODE_TYPE
    TEMPLATE_SRC="$SCRIPT_DIR/docker-cloud-init.yml.tmpl"
    # Reject missing trust before issuing a single-use enrollment token.
    test -s "${OPENVOX_CA_CERT:?Set authenticated OpenVox CA bundle path}"
    create_vault_token "$VM_FQDN"
    OPENVOX_CA_BUNDLE=$(sed 's/^/      /' "$OPENVOX_CA_CERT")
    export OPENVOX_CA_BUNDLE
    CONFIGURE_PUPPET_SCRIPT=$(sed 's/^/      /' "$SCRIPT_DIR/configure-puppet.sh")
    export CONFIGURE_PUPPET_SCRIPT
    INSTALL_OPENVOX_SCRIPT=$(sed 's/^/      /' "$SCRIPT_DIR/install-openvox.sh")
    export INSTALL_OPENVOX_SCRIPT
    PUPPET_ENROLLMENT_COMMAND=$(jq -cn --arg type "$NODE_TYPE" --arg token "$VM_TOKEN" --arg certname "$VM_FQDN" --arg server "$PUPPET_SERVER" \
      '["/usr/local/bin/configure-puppet.sh", $type, $token, $certname, $server]')
    export PUPPET_ENROLLMENT_COMMAND
    install -m 0600 /dev/null "$GENERATED_YAML"
    envsubst '${NEW_VM_NAME}${VM_FQDN}${IPV6_POOL}${IPV6_FIXED}${DOMAIN_SUFFIX}${CONFIGURE_PUPPET_SCRIPT}${INSTALL_OPENVOX_SCRIPT}${OPENVOX_CA_BUNDLE}${PUPPET_ENROLLMENT_COMMAND}' < "$TEMPLATE_SRC" > "$GENERATED_YAML"
fi

# 2. Only remove a previously designated development guest; recheck ownership.
CLONED=false
cleanup() {
    local status=$?
    trap - EXIT
    rm -f "$GENERATED_YAML"
    if [[ $status != 0 && $CLONED == true ]]; then
        remove_target "$VMID" || echo 'Retaining VM: cleanup could not verify ownership.' >&2
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if [[ $TARGET_EXISTS == true ]]; then remove_target "$VMID"; fi
qm clone "$TEMPLATE_ID" "$VMID" --name "$NEW_VM_NAME" --description "$OWNER" --storage "$STORAGE" --full 1
CLONED=true
qm set "$VMID" --cores "$CORES" --memory "$MEMORY" --agent 1 --cpu host \
    --bios ovmf --machine q35 \
    --efidisk0 "${STORAGE}:0,efitype=4m,pre-enrolled-keys=0" --scsihw virtio-scsi-single
qm resize "$VMID" scsi0 "$DISK_SIZE"
qm set "$VMID" \
    --cicustom "vendor=${SNIPPET_STORAGE}:snippets/$(basename "$GENERATED_YAML")" \
    --ipconfig0 ip=dhcp --searchdomain "$DOMAIN_SUFFIX" --ciuser user_l --sshkey ~/.ssh/id_ed25519.pub
mkdir -p "$LOG_DIR"
qm start "$VMID"
wait_for_cloudinit "$VMID"
verify_guest_identity "$VMID" "$NEW_VM_NAME" "$VM_FQDN"
guest_exec "$VMID" cat /var/log/cloud-init-output.log >> "$LOG_DIR/cloud-init-output.log"
qm set "$VMID" --delete cicustom
# Keep the cloud-init disk: its instance ID must survive reboot.
reboot_and_verify_guest "$VMID" "$NEW_VM_NAME" "$VM_FQDN"
echo "Deployment complete: $VM_FQDN (VM $VMID)."
