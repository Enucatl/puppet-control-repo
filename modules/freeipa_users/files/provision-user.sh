#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C
umask 077

check=false
if [[ ${1:-} == --check ]]; then
  check=true
  shift
fi
if (( $# < 4 || $# > 5 )) || [[ ! $1 =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.-]*$ ]]; then
  echo 'Usage: puppet-ipa-provision-user [--check] USER FIRST LAST SHELL [KEYTAB]' >&2
  exit 2
fi
username=$1 first=$2 last=$3 shell=$4 keytab=${5:-}
if [[ -n $keytab && $keytab != /* ]]; then
  echo 'The keytab path must be absolute.' >&2
  exit 2
fi

work=$(mktemp -d)
trap 'kdestroy -c "FILE:$work/provisioner" 2>/dev/null || :; kdestroy -c "FILE:$work/user" 2>/dev/null || :; rm -rf -- "$work"' EXIT
export KRB5CCNAME="FILE:$work/provisioner"
kinit -k -t /etc/puppet-ipa-provisioner.keytab 'puppet-provisioner/docker.home.arpa@HOME.ARPA'

# Only FreeIPA's explicit NotFound response permits creation. A failed lookup
# (including an authentication or network error) must never mean "absent".
if ! ipa user-show "$username" --all --raw >"$work/user-info" 2>"$work/error"; then
  if ! grep -Fxq "ipa: ERROR: $username: user not found" "$work/error"; then
    cat "$work/error" >&2
    exit 2
  fi
  $check && exit 1
  ipa user-add "$username" --first="$first" --last="$last" --shell="$shell"
  ipa user-show "$username" --all --raw >"$work/user-info"
fi

[[ -n $keytab ]] || exit 0
# Authenticate against the KDC, rather than accepting a stale keytab merely
# because it contains an entry with the expected principal name.
if [[ -s $keytab ]] && kinit -c "FILE:$work/user" -k -t "$keytab" "$username@HOME.ARPA" 2>"$work/keytab-error"; then
  exit 0
fi
$check && exit 1

# Never rekey an existing principal to recover a missing/stale local file.
if grep -Eq '^  has_keytab: (TRUE|True)$' "$work/user-info"; then
  ipa-getkeytab -r -s freeipa.home.arpa -p "$username@HOME.ARPA" -k "$work/keytab"
elif grep -Eq '^  has_keytab: (FALSE|False)$' "$work/user-info"; then
  ipa-getkeytab -s freeipa.home.arpa -p "$username@HOME.ARPA" -k "$work/keytab"
else
  echo "Cannot determine whether $username has Kerberos keys; refusing to rekey." >&2
  exit 2
fi
kinit -c "FILE:$work/user" -k -t "$work/keytab" "$username@HOME.ARPA"
install -o root -g root -m 0400 "$work/keytab" "$keytab"
