#!/bin/bash
set -euo pipefail

# Run as root on proxmox.home.arpa. It creates one disposable Ubuntu 26.04
# client from the shared template. Successful runs destroy it; failed runs retain it
# and its cloud-init snippet for diagnosis.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/config.sh"
source "${SCRIPT_DIR}/lib.sh"
[[ ! -f .env ]] || load_env

export VAULT_ADDR="${VAULT_ADDR:-https://hcv.home.arpa:8200}"

VMID="${1:-$(pvesh get /cluster/nextid)}"
VMNAME="freeipa-acceptance-${VMID}"
VM_FQDN="${VMNAME}.${DOMAIN_SUFFIX}"
NEW_VM_NAME="$VMNAME"
OWNER="openvox-provisioning:freeipa-acceptance:${VM_FQDN}"
validate_target "$VMID" "$VMNAME" "$OWNER" false
check_provisioning_host
VAULT_TOKEN="${VAULT_TOKEN:-$(vault token lookup -format=json | jq -er '.data.id')}"
export VAULT_TOKEN
SNIPPET="/var/lib/vz/snippets/freeipa-client-acceptance-${VMID}.yml"
readonly NODE_TYPE='freeipa_acceptance'
readonly STORAGE="${DEFAULT_STORAGE}"
passed=false

cleanup() {
  if [[ "${passed}" != true ]]; then
    echo "Acceptance failed; retaining ${VMNAME} (${VMID}) and ${SNIPPET}." >&2
    return
  fi
  remove_target "$VMID"
  rm -f "${SNIPPET}"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

create_vault_token "$VM_FQDN"
OPENVOX_CA_BUNDLE=$(sed 's/^/      /' "${OPENVOX_CA_CERT:?Set authenticated OpenVox CA bundle path}")
export OPENVOX_CA_BUNDLE
export VM_TOKEN NODE_TYPE PUPPET_SERVER VM_FQDN NEW_VM_NAME
INSTALL_OPENVOX_SCRIPT=$(sed 's/^/      /' "$SCRIPT_DIR/install-openvox.sh")
export INSTALL_OPENVOX_SCRIPT
PUPPET_ENROLLMENT_COMMAND=$(jq -cn --arg certname "$VM_FQDN" --arg token "$VM_TOKEN" --arg type "$NODE_TYPE" --arg server "$PUPPET_SERVER" \
  '["/usr/local/sbin/configure-puppet-acceptance", $certname, $token, $type, $server]')
export PUPPET_ENROLLMENT_COMMAND
install -m 0600 /dev/null "$SNIPPET"
envsubst '${NEW_VM_NAME}${VM_FQDN}${INSTALL_OPENVOX_SCRIPT}${OPENVOX_CA_BUNDLE}${PUPPET_ENROLLMENT_COMMAND}' \
  < "${SCRIPT_DIR}/freeipa-client-acceptance-cloud-init.yml.tmpl" > "${SNIPPET}"

qm clone "${TEMPLATE_ID}" "${VMID}" --name "${VMNAME}" --description "$OWNER" --storage "${STORAGE}" --full 1
qm set "${VMID}" --cores 2 --memory 2048 --agent 1 --ipconfig0 ip=dhcp \
  --searchdomain "$DOMAIN_SUFFIX" \
  --cicustom "vendor=${SNIPPET_STORAGE}:snippets/$(basename "${SNIPPET}")"
qm start "${VMID}"

wait_for_cloudinit "$VMID"
verify_guest_identity "$VMID" "$VMNAME" "$VM_FQDN"

run_guest() {
  local result exit_code output
  result="$(qm guest exec "${VMID}" --timeout 3600 -- "$@")"
  output="$(jq -r '."out-data" // ."err-data" // empty' <<<"${result}")"
  printf '%s\n' "${output}"
  exit_code="$(jq -r '.exitcode // 1' <<<"${result}")"
  return "${exit_code}"
}

run_guest /bin/bash -lc '
  test -f /etc/ipa/default.conf
  grep -Eq "^[[:space:]]*domain[[:space:]]*=[[:space:]]*home\.arpa[[:space:]]*$" /etc/ipa/default.conf
  grep -Eq "^[[:space:]]*server[[:space:]]*=[[:space:]]*freeipa\.home\.arpa[[:space:]]*$" /etc/ipa/default.conf
  test -s /etc/krb5.keytab
  systemctl is-enabled sssd
  systemctl is-active sssd
  /opt/puppetlabs/puppet/bin/ruby -r /usr/local/libexec/openvox-child-policy.rb \
    /opt/puppetlabs/puppet/bin/puppet agent --test --tags freeipa --detailed-exitcodes
'

passed=true
echo "FreeIPA client acceptance passed for ${VMNAME}; destroying ${VMID}."
echo "After the VM is removed, clean its FreeIPA host and Puppet CA certificate:"
echo "  ipa host-del ${VM_FQDN}"
echo "  sudo /opt/puppetlabs/bin/puppetserver ca clean --certname ${VM_FQDN}"
