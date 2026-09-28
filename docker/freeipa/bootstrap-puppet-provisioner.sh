#!/usr/bin/env bash
set -euo pipefail
set +x
export LC_ALL=C
umask 077

# Run as root on docker.home.arpa with administrative Vault authentication.
# Passwords and Kerberos tickets stay outside Puppet catalogs and the repository.
[[ $EUID == 0 && $(hostname -f) == docker.home.arpa ]] || {
  echo 'Run this bootstrap as root on docker.home.arpa.' >&2
  exit 1
}
readonly principal='puppet-provisioner/docker.home.arpa@HOME.ARPA'
readonly keytab='/etc/puppet-ipa-provisioner.keytab'
readonly role='Puppet User Provisioner'
readonly privilege='Puppet User Provisioning'
readonly basedn='dc=home,dc=arpa'
readonly backrest_dn="uid=backrest,cn=users,cn=accounts,$basedn"
work=$(mktemp -d /run/puppet-ipa-bootstrap.XXXXXX)
export KRB5CCNAME="FILE:$work/admin.ccache"
cleanup() {
  kdestroy -c "FILE:$work/admin.ccache" >/dev/null 2>&1 || true
  kdestroy -c "FILE:$work/check.ccache" >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if password=$(vault kv get -field=admin_password kv/freeipa-admin 2>"$work/vault.err"); then
  :
elif grep -Fxq 'No value found at kv/data/freeipa-admin' "$work/vault.err"; then
  password=$(vault kv get -field='freeipa_users::admin_password' kv/puppet)
else
  cat "$work/vault.err" >&2
  exit 1
fi
printf '%s\n' "$password" | kinit admin@HOME.ARPA >/dev/null
unset password

# Only a confirmed missing IPA object permits creation; authentication and
# transport failures must stop the bootstrap.
ensure_object() {
  local kind=$1 name=$2
  shift 2
  if ipa "$kind-show" "$name" --all --raw >"$work/object" 2>"$work/ipa.err"; then
    return
  fi
  if ! grep -Fxq "ipa: ERROR: $name: $kind not found" "$work/ipa.err"; then
    cat "$work/ipa.err" >&2
    return 1
  fi
  ipa "$kind-add" "$name" "$@" >/dev/null
}

ensure_object service "$principal"
ensure_object role "$role" --desc='Docker-only Puppet user creation'
ensure_object privilege "$privilege" --desc='Create users and manage only Backrest keys'

# Virtual protected-operation attributes authorize the keytab extended
# operation, without granting password resets or direct key material reads.
ensure_object permission 'Puppet Retrieve Backrest Keys' \
  --right=read --attrs='ipaProtectedOperation;read_keys' \
  --subtree="cn=users,cn=accounts,$basedn" --target="$backrest_dn"
ensure_object permission 'Puppet Create Backrest Keys' \
  --right=write --attrs='ipaProtectedOperation;write_keys' \
  --subtree="cn=users,cn=accounts,$basedn" --target="$backrest_dn"
# user-show computes has_keytab with an LDAP presence search. Search alone
# reveals whether keys exist, never their value.
ensure_object permission 'Puppet Check Backrest Keys' \
  --right=search --attrs=krbPrincipalKey \
  --subtree="cn=users,cn=accounts,$basedn" --target="$backrest_dn"

permissions=(
  'System: Add Users'
  'System: Add User to default group'
  'System: Read UPG Definition'
  'Puppet Retrieve Backrest Keys'
  'Puppet Create Backrest Keys'
  'Puppet Check Backrest Keys'
)
for permission in "${permissions[@]}"; do
  ipa privilege-show "$privilege" --all --raw >"$work/privilege"
  if ! grep -Fxiq "  memberof: cn=$permission,cn=permissions,cn=pbac,$basedn" "$work/privilege"; then
    ipa privilege-add-permission "$privilege" --permissions="$permission" >/dev/null
  fi
done
ipa role-show "$role" --all --raw >"$work/role"
if ! grep -Fxiq "  memberof: cn=$privilege,cn=privileges,cn=pbac,$basedn" "$work/role"; then
  ipa role-add-privilege "$role" --privileges="$privilege" >/dev/null
fi
if ! grep -Fxiq "  member: krbprincipalname=$principal,cn=services,cn=accounts,$basedn" "$work/role"; then
  ipa role-add-member "$role" --services="$principal" >/dev/null
fi

# Administrators need an explicit retrieval grant to recover this service's
# existing keys; their ordinary keytab permission authorizes only creation.
ipa service-show "$principal" --all --raw >"$work/service"
if ! grep -Fxiq "  ipaallowedtoperform;read_keys: cn=admins,cn=groups,cn=accounts,$basedn" "$work/service"; then
  ipa service-allow-retrieve-keytab "$principal" --groups=admins >/dev/null
fi
if [[ -f $keytab ]] && KRB5CCNAME="FILE:$work/check.ccache" kinit -kt "$keytab" "$principal" 2>"$work/keytab.err"; then
  chown root:root "$keytab"
  chmod 0400 "$keytab"
else
  ipa service-show "$principal" --all --raw >"$work/service"
  retrieve=()
  if grep -Fxq '  has_keytab: TRUE' "$work/service"; then
    retrieve=(-r)
  elif ! grep -Fxq '  has_keytab: FALSE' "$work/service"; then
    echo 'FreeIPA did not report whether the provisioner has keys; refusing to rekey.' >&2
    exit 1
  fi
  ipa-getkeytab "${retrieve[@]}" -p "$principal" -k "$work/provisioner.keytab"
  KRB5CCNAME="FILE:$work/check.ccache" kinit -kt "$work/provisioner.keytab" "$principal"
  install -o root -g root -m 0400 "$work/provisioner.keytab" "$keytab"
fi
echo "Provisioner ready: $principal ($keytab)."
