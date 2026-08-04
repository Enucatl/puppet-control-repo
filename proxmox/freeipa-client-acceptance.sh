#!/bin/bash
set -euo pipefail

# Run as root on proxmox.home.arpa. It creates one disposable Ubuntu 24.04
# client from template 9000. Successful runs destroy it; failed runs retain it
# and its cloud-init snippet for diagnosis.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/config.sh"
source "${SCRIPT_DIR}/lib.sh"

export VAULT_ADDR="${VAULT_ADDR:-https://hcv.home.arpa:8200}"
vault token lookup -format=json >/dev/null

VMID="${1:-$(pvesh get /cluster/nextid)}"
VMNAME="freeipa-acceptance-${VMID}"
VM_FQDN="${VMNAME}.home.arpa"
SNIPPET="/var/lib/vz/snippets/freeipa-client-acceptance-${VMID}.yml"
readonly NODE_TYPE='freeipa_acceptance'
readonly STORAGE="${DEFAULT_STORAGE}"
passed=false

cleanup() {
  if [[ "${passed}" != true ]]; then
    echo "Acceptance failed; retaining ${VMNAME} (${VMID}) and ${SNIPPET}." >&2
    return
  fi
  qm stop "${VMID}" >/dev/null 2>&1 || true
  qm destroy "${VMID}" --purge >/dev/null 2>&1 || true
  rm -f "${SNIPPET}"
}
trap cleanup EXIT INT TERM

VM_TOKEN="$(vault token create -policy=puppet -ttl=2h -renewable -format=json | jq -r '.auth.client_token')"
if [[ -z "${VM_TOKEN}" || "${VM_TOKEN}" == 'null' ]]; then
  echo 'Unable to create the Puppet autosign token.' >&2
  exit 1
fi
export VM_TOKEN NODE_TYPE PUPPET_SERVER VM_FQDN
envsubst '${VM_TOKEN}${NODE_TYPE}${PUPPET_SERVER}${VM_FQDN}' \
  < "${SCRIPT_DIR}/freeipa-client-acceptance-cloud-init.yml.tmpl" > "${SNIPPET}"

qm clone "${TEMPLATE_ID}" "${VMID}" --name "${VMNAME}" --storage "${STORAGE}" --full 1
qm set "${VMID}" --cores 2 --memory 2048 --agent 1 --ipconfig0 ip=dhcp \
  --cicustom "vendor=${SNIPPET_STORAGE}:snippets/$(basename "${SNIPPET}")"
qm start "${VMID}"

wait_for_acceptance_cloudinit() {
  local status
  echo 'Waiting for Cloud-Init to finish...'
  while true; do
    # The QEMU guest agent starts after the VM.  Until then, treat its
    # absence as a pending cloud-init state rather than a test failure.
    status="$(qm guest exec "${VMID}" -- cloud-init status 2>/dev/null \
      | jq -r '."out-data" // empty' || true)"
    if [[ "${status}" == *'status: done'* ]]; then
      echo '[Success] Cloud-Init reports done.'
      return
    fi
    if [[ "${status}" == *'status: error'* ]]; then
      echo 'Cloud-init failed; retaining its output in this test log:' >&2
      qm guest exec "${VMID}" -- /bin/bash -lc 'tail -200 /var/log/cloud-init-output.log' \
        | jq -r '."out-data" // ."err-data" // empty' >&2 || true
      return 1
    fi
    sleep 10
  done
}

wait_for_acceptance_cloudinit

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
  /opt/puppetlabs/bin/puppet agent --test --tags freeipa --detailed-exitcodes
'

passed=true
echo "FreeIPA client acceptance passed for ${VMNAME}; destroying ${VMID}."
echo "After the VM is removed, clean its FreeIPA host and Puppet CA certificate:"
echo "  ipa host-del ${VM_FQDN}"
echo "  sudo /opt/puppetlabs/bin/puppetserver ca clean --certname ${VM_FQDN}"
