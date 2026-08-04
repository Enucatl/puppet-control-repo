#!/usr/bin/env bash
set -euo pipefail

# Run on an enrolled FreeIPA client with an admin Kerberos ticket and Vault
# authentication. The ipa CLI performs the server-side operations remotely.
# Set ROTATE_ENROLLER_PASSWORD=1 to rotate an existing account deliberately.

readonly ENROLLER_USER='puppet-enroller'
readonly ENROLLER_ROLE='Puppet Host Enroller'
readonly ENROLLER_GROUP='puppet-enrollers'
readonly PASSWORD_FIELD='freeipa::client::password'
readonly VAULT_PATH="${VAULT_PATH:-kv/puppet}"
readonly VAULT_MOUNT="${VAULT_PATH%%/*}"
readonly VAULT_SECRET_PATH="${VAULT_PATH#*/}"
readonly VAULT_API_PATH="${VAULT_MOUNT}/data/${VAULT_SECRET_PATH}"

if ! klist -s; then
  echo 'An existing FreeIPA admin Kerberos ticket is required.' >&2
  exit 1
fi

if ! vault token lookup -format=json >/dev/null 2>&1; then
  echo 'An authenticated Vault CLI context is required.' >&2
  exit 1
fi

vault_capabilities="$(vault token capabilities -format=json "${VAULT_API_PATH}")"
if ! grep -Fq '"read"' <<<"${vault_capabilities}" || ! grep -Fq '"update"' <<<"${vault_capabilities}"; then
  echo "Vault authentication must read and update ${VAULT_PATH}." >&2
  exit 1
fi

password="$(vault kv get -field="${PASSWORD_FIELD}" "${VAULT_PATH}" 2>/dev/null || true)"
password_missing=false
if [[ -z "${password}" ]]; then
  password_missing=true
fi
if [[ "${password_missing}" == true || "${ROTATE_ENROLLER_PASSWORD:-0}" == '1' ]]; then
  password="$(openssl rand -base64 36)"
fi

ipa role-show "${ENROLLER_ROLE}" >/dev/null 2>&1 || ipa role-add "${ENROLLER_ROLE}" \
  --desc='Least-privilege Puppet host enrollment'

for privilege in 'Host Administrators' 'Host Enrollment'; do
  if ! ipa role-show "${ENROLLER_ROLE}" --all | grep -Fq "${privilege}"; then
    ipa role-add-privilege "${ENROLLER_ROLE}" --privileges="${privilege}"
  fi
done

ipa group-show "${ENROLLER_GROUP}" >/dev/null 2>&1 || ipa group-add "${ENROLLER_GROUP}" \
  --desc='Puppet FreeIPA enrollment credential policy group'

# FreeIPA represents password expiry as a finite timestamp. Its supported
# 10,000-day maximum is operationally non-expiring while retaining a policy
# that can still be changed deliberately during credential rotation.
ipa pwpolicy-show "${ENROLLER_GROUP}" >/dev/null 2>&1 || \
  ipa pwpolicy-add "${ENROLLER_GROUP}" --priority=1 --maxlife=10000 --minlife=0
if ! ipa pwpolicy-show "${ENROLLER_GROUP}" --all | grep -Fq 'Max lifetime (days): 10000'; then
  ipa pwpolicy-mod "${ENROLLER_GROUP}" --maxlife=10000 --minlife=0
fi

if ! ipa user-show "${ENROLLER_USER}" >/dev/null 2>&1; then
  printf '%s\n%s\n' "${password}" "${password}" | ipa user-add "${ENROLLER_USER}" \
    --first='Puppet' --last='Enroller' --password
fi

if ! ipa group-show "${ENROLLER_GROUP}" --all --raw | grep -Fq "member: uid=${ENROLLER_USER},cn=users,"; then
  ipa group-add-member "${ENROLLER_GROUP}" --users="${ENROLLER_USER}"
fi

# A prior partial bootstrap may have created the user before its policy group.
# Reset its password after group membership so the non-expiring policy applies.
if [[ "${password_missing}" == true || "${ROTATE_ENROLLER_PASSWORD:-0}" == '1' ]]; then
  printf '%s\n%s\n' "${password}" "${password}" | ipa passwd "${ENROLLER_USER}"
fi

# An administrator password reset marks the password as immediately expired.
# Clear that one-time-change state while preserving the policy's long but
# finite credential lifetime for the noninteractive enrollment principal.
ipa user-mod "${ENROLLER_USER}" \
  --password-expiration="$(date -u -d '+10000 days' +%Y%m%d%H%M%SZ)" >/dev/null

if ! ipa role-show "${ENROLLER_ROLE}" --all --raw | grep -Fq "member: uid=${ENROLLER_USER},cn=users,"; then
  ipa role-add-member "${ENROLLER_ROLE}" --users="${ENROLLER_USER}"
fi

# Vault accepts a '-' value from stdin, avoiding a credential-bearing argv.
printf '%s' "${password}" | vault kv patch "${VAULT_PATH}" "${PASSWORD_FIELD}=-" >/dev/null
echo "${ENROLLER_USER} and ${ENROLLER_ROLE} are ready; ${PASSWORD_FIELD} is stored in Vault."
