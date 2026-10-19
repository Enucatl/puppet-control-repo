#!/bin/bash
set -euo pipefail
# Create the separate template, then prove two clean Atlas base deployments.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/config.sh"
source "$SCRIPT_DIR/lib.sh"
[[ ! -f .env ]] || load_env
REBUILD=false
while getopts 'rh' opt; do
    case $opt in
        r) REBUILD=true ;;
        h) echo "Usage: $0 [-r]"; exit 0 ;;
        *) exit 1 ;;
    esac
done
LOG_DIR="${LOG_DIR:-/var/log/openvox-provisioning}"
export LOG_DIR
check_provisioning_host
mkdir -p "$LOG_DIR"
EVIDENCE="$LOG_DIR/atlas-acceptance"
mkdir -p "$EVIDENCE"
# Atlas is explicitly designated 210; the template must use a separate ID.
validate_target 210 atlas openvox-provisioning:docker:atlas.home.arpa "$REBUILD"
protected_configs() {
    local config
    for config in /etc/pve/qemu-server/*.conf /etc/pve/lxc/*.conf; do
        [[ -f $config ]] || continue
        [[ ${config##*/} != 210.conf && ${config##*/} != "$TEMPLATE_ID.conf" ]] || continue
        sha256sum "$config"
    done
}
protected_configs > "$EVIDENCE/protected-before.sha256"
if qm config "$TEMPLATE_ID" >/dev/null 2>&1; then
    TARGET_IS_TEMPLATE=1 validate_target "$TEMPLATE_ID" ubuntu-server-2604-template \
        openvox-provisioning:template:ubuntu-26.04 true
    qm config "$TEMPLATE_ID" | grep -q '^template: 1$'
else
    bash "$SCRIPT_DIR/ubuntu-server-template.sh" -i "$TEMPLATE_ID"
fi

check_atlas() {
    local run="$1" address host_key
    qm config 210 > "$EVIDENCE/run-$run-config.txt"
    grep -qx 'memory: 8192' "$EVIDENCE/run-$run-config.txt"
    grep -qx 'name: atlas' "$EVIDENCE/run-$run-config.txt"
    grep -Eq '^scsi0: .*[, ]size=300G(,|$)' "$EVIDENCE/run-$run-config.txt"
    [[ $(grep -c '^scsi[0-9]' "$EVIDENCE/run-$run-config.txt") == 1 ]]
    verify_guest_identity 210 atlas atlas.home.arpa
    guest_exec 210 bash -c '
        set -e
        . /etc/os-release
        test "$VERSION_ID" = 26.04
        test "$(stat -c %d /scratch)" = "$(stat -c %d /)"
        test "$(stat -c %U /scratch/apt-cacher-ng)" = apt-cacher-ng
        test "$(stat -c %a /scratch/backup/vault)" = 700
        test ! -e /etc/puppetlabs/puppet/ssl/private_keys
        test ! -e /etc/puppetlabs/puppet/csr_attributes.yaml
        ! systemctl is-active --quiet puppet.service
        systemctl is-active --quiet qemu-guest-agent
        cloud-init status --long
        dpkg-query -W openvox-agent cloud-init qemu-guest-agent
        cat /etc/hostname /etc/hosts
    ' > "$EVIDENCE/run-$run-guest.txt"
    address=$(qm guest cmd 210 network-get-interfaces | jq -er '
        [.[] | select(.name != "lo") | ."ip-addresses"[]? |
          select(."ip-address-type" == "ipv4" and (."ip-address" | startswith("169.254.") | not)) |
          ."ip-address"][0] // empty')
    host_key=$(guest_exec 210 cat /etc/ssh/ssh_host_ed25519_key.pub)
    printf '%s %s\n' "$address" "$host_key" > "$EVIDENCE/known_hosts"
    ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=yes \
        -o UserKnownHostsFile="$EVIDENCE/known_hosts" "user_l@$address" \
        'test "$(hostname)" = atlas && test "$(hostname -f)" = atlas.home.arpa && sudo -n true'
    printf 'run=%s address=%s hostname=atlas fqdn=atlas.home.arpa ssh=passed\n' "$run" "$address" \
        | tee "$EVIDENCE/run-$run-result.txt"
}
FIRST_ARGS=()
if [[ $REBUILD == true ]]; then FIRST_ARGS=(-r); fi
bash "$SCRIPT_DIR/atlas-server.sh" -t "$TEMPLATE_ID" "${FIRST_ARGS[@]}"
check_atlas 1
bash "$SCRIPT_DIR/atlas-server.sh" -t "$TEMPLATE_ID" -r
check_atlas 2
protected_configs > "$EVIDENCE/protected-after.sha256"
cmp "$EVIDENCE/protected-before.sha256" "$EVIDENCE/protected-after.sha256"
echo "Atlas acceptance passed twice; unrelated VM/template configurations unchanged. Evidence: $EVIDENCE"
