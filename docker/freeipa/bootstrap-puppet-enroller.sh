#!/usr/bin/env bash
set -euo pipefail

# Run inside the FreeIPA server container with an existing admin Kerberos
# ticket. VAULT_ADDR/VAULT_CACERT and Vault authentication must also be set.
# Set ROTATE_ENROLLER_PASSWORD=1 to rotate an existing account deliberately.

readonly ENROLLER_USER='puppet-enroller'
readonly ENROLLER_ROLE='Puppet Host Enroller'
readonly PASSWORD_FIELD='freeipa-client-enrollment-password'
readonly VAULT_PATH="${VAULT_PATH:-kv/puppet}"

if ! klist -s; then
  echo 'An existing FreeIPA admin Kerberos ticket is required.' >&2
  exit 1
fi

password="$(vault kv get -field="${PASSWORD_FIELD}" "${VAULT_PATH}" 2>/dev/null || true)"
if [[ -z "${password}" || "${ROTATE_ENROLLER_PASSWORD:-0}" == '1' ]]; then
  password="$(openssl rand -base64 36)"
fi

if ! ipa user-show "${ENROLLER_USER}" >/dev/null 2>&1; then
  printf '%s\n%s\n' "${password}" "${password}" | ipa user-add "${ENROLLER_USER}" \
    --first='Puppet' --last='Enroller' --password
elif [[ "${ROTATE_ENROLLER_PASSWORD:-0}" == '1' ]]; then
  printf '%s\n%s\n' "${password}" "${password}" | ipa passwd "${ENROLLER_USER}"
fi

ipa role-show "${ENROLLER_ROLE}" >/dev/null 2>&1 || ipa role-add "${ENROLLER_ROLE}" \
  --desc='Least-privilege Puppet host enrollment'

for privilege in 'Host Administrators' 'Host Enrollment'; do
  if ! ipa role-show "${ENROLLER_ROLE}" --all | grep -Fq "${privilege}"; then
    ipa role-add-privilege "${ENROLLER_ROLE}" --privileges="${privilege}"
  fi
done

if ! ipa role-show "${ENROLLER_ROLE}" --all | grep -Eq "Member users:.*(^|, )${ENROLLER_USER}(,|$)"; then
  ipa role-add-member "${ENROLLER_ROLE}" --users="${ENROLLER_USER}"
fi

# The role is also an IPA group, so a group password policy can make this
# automation credential non-expiring without weakening the global policy.
ipa pwpolicy-show "${ENROLLER_ROLE}" >/dev/null 2>&1 || \
  ipa pwpolicy-add "${ENROLLER_ROLE}" --priority=1 --maxlife=0 --minlife=0

vault kv patch "${VAULT_PATH}" "${PASSWORD_FIELD}=${password}" >/dev/null
echo "${ENROLLER_USER} and ${ENROLLER_ROLE} are ready; ${PASSWORD_FIELD} is stored in Vault."
