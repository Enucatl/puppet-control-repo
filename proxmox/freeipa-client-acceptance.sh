#!/bin/bash
set -euo pipefail

# Run as root on proxmox.home.arpa. It creates one disposable Ubuntu 24.04
# client from template 9000 and always destroys the VM on exit.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/config.sh"
source "${SCRIPT_DIR}/lib.sh"

export VAULT_ADDR="${VAULT_ADDR:-https://hcv.home.arpa:8200}"
vault token lookup -format=json >/dev/null

VMID="${1:-$(pvesh get /cluster/nextid)}"
VMNAME="freeipa-acceptance-${VMID}"
SNIPPET="/var/lib/vz/snippets/freeipa-client-acceptance-${VMID}.yml"
readonly NODE_TYPE='freeipa_acceptance'
readonly STORAGE="${DEFAULT_STORAGE}"

cleanup() {
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
export VM_TOKEN NODE_TYPE PUPPET_SERVER
envsubst '${VM_TOKEN}${NODE_TYPE}${PUPPET_SERVER}' \
  < "${SCRIPT_DIR}/freeipa-client-acceptance-cloud-init.yml.tmpl" > "${SNIPPET}"

qm clone "${TEMPLATE_ID}" "${VMID}" --name "${VMNAME}" --storage "${STORAGE}" --full 1
qm set "${VMID}" --cores 2 --memory 2048 --agent 1 --ipconfig0 ip=dhcp \
  --cicustom "vendor=${SNIPPET_STORAGE}:snippets/$(basename "${SNIPPET}")"
qm start "${VMID}"
wait_for_cloudinit "${VMID}"

run_guest() {
  local result pid status exit_code output
  result="$(qm guest exec "${VMID}" -- "$@")"
  pid="$(jq -r '.pid' <<<"${result}")"
  while true; do
    status="$(qm guest exec-status "${VMID}" "${pid}")"
    if [[ "$(jq -r '.exited' <<<"${status}")" == true ]]; then
      output="$(jq -r '."out-data" // ."err-data" // empty' <<<"${status}")"
      printf '%s\n' "${output}"
      exit_code="$(jq -r '.exitcode // 1' <<<"${status}")"
      return "${exit_code}"
    fi
    sleep 2
  done
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

echo "FreeIPA client acceptance passed for ${VMNAME}; destroying ${VMID}."
