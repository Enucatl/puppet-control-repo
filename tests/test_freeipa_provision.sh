#!/usr/bin/env bash
# Isolated checks: no FreeIPA access and no writes outside the temporary directory.
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
mkdir "$work/bin" "$work/tmp"
export TEST_WORK=$work TMPDIR=$work/tmp
export PATH=$work/bin:$PATH

cat > "$work/bin/mock" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
command=${0##*/}
printf '%s' "$command" >> "$TEST_WORK/argv"
printf ' %q' "$@" >> "$TEST_WORK/argv"
printf '\n' >> "$TEST_WORK/argv"
case $command in
  kinit)
    cache=${KRB5CCNAME#FILE:}
    if [[ $1 == -c ]]; then cache=${2#FILE:}; fi
    [[ $(stat -c %a "${cache%/*}") == 700 ]]
    printf '%s\n' "${cache%/*}" >> "$TEST_WORK/caches"
    touch "$cache"
    if [[ $1 != -c ]]; then
      [[ $* == '-k -t /etc/puppet-ipa-provisioner.keytab puppet-provisioner/docker.home.arpa@HOME.ARPA' ]]
      [[ $TEST_CASE != auth-fail ]]
    else
      [[ ${*: -1} == backrest@HOME.ARPA ]]
      [[ $TEST_CASE != key-auth-fail ]]
      [[ ${5} != "$TEST_WORK/local keytab" || $TEST_CASE == valid ]]
    fi ;;
  kdestroy) ;;
  ipa)
    case $1 in
      user-show)
        case $TEST_CASE in
          network) echo 'ipa: ERROR: cannot connect to server' >&2; exit 1 ;;
          lookup-auth) echo 'ipa: ERROR: Ticket expired' >&2; exit 1 ;;
          absent)
            if [[ ! -f $TEST_WORK/created ]]; then
              echo "ipa: ERROR: $2: user not found" >&2
              exit 2
            fi ;;
        esac
        printf '  has_keytab: %s\n' "$KEY_STATE" ;;
      user-add)
        printf '%s\0' "$@" > "$TEST_WORK/add-argv"
        touch "$TEST_WORK/created" ;;
      *) exit 99 ;;
    esac ;;
  ipa-getkeytab)
    [[ $TEST_CASE != retrieve-fail ]]
    [[ ${*: -1} == "$TMPDIR/"*/keytab ]]
    printf 'new-key\n' > "${*: -1}" ;;
  install)
    [[ $1 == -o && $2 == root && $3 == -g && $4 == root && $5 == -m && $6 == 0400 ]]
    cp -- "$7" "$8"
    chmod 0400 "$8" ;;
  *) exit 99 ;;
esac
MOCK
chmod +x "$work/bin/mock"
for command in ipa kinit kdestroy ipa-getkeytab install; do
  ln -s mock "$work/bin/$command"
done

reset_case() {
  export TEST_CASE=$1 KEY_STATE=${2:-TRUE}
  rm -f "$work/created" "$work/add-argv" "$work/local keytab"
  : > "$work/argv"
  : > "$work/caches"
}
run() {
  local expected=$1 actual=0 cache
  shift
  bash "$repo/modules/freeipa_users/files/provision-user.sh" "$@" > "$work/output" 2>&1 || actual=$?
  if [[ $actual != "$expected" ]]; then
    cat "$work/output" >&2
    echo "$TEST_CASE: expected status $expected, got $actual" >&2
    exit 1
  fi
  while IFS= read -r cache; do
    [[ ! -e $cache ]]
    grep -Fqx "kdestroy -c FILE:$cache/provisioner" "$work/argv"
    grep -Fqx "kdestroy -c FILE:$cache/user" "$work/argv"
  done < "$work/caches"
  [[ -z $(find "$TMPDIR" -mindepth 1 -print -quit) ]]
}
no_mutations() {
  ! grep -Eq '^(ipa user-add|ipa-getkeytab|install) ' "$work/argv"
}

for test_case in network lookup-auth; do
  reset_case "$test_case"
  run 2 backrest Back Rest /bin/bash
  no_mutations
done
reset_case auth-fail
run 1 backrest Back Rest /bin/bash
no_mutations
! grep -q '^ipa ' "$work/argv"

reset_case absent
run 1 --check backrest Back Rest /bin/bash
no_mutations
run 0 backrest 'Back $(touch forbidden)' "O'Rest; *" '/bin/bash; false'
printf '%s\0' user-add backrest '--first=Back $(touch forbidden)' "--last=O'Rest; *" '--shell=/bin/bash; false' > "$work/expected-argv"
cmp "$work/expected-argv" "$work/add-argv"

reset_case valid
printf 'existing-key\n' > "$work/local keytab"
run 0 backrest Back Rest /bin/bash "$work/local keytab"
[[ $(cat "$work/local keytab") == existing-key ]]
grep -q '^kinit -c .* backrest@HOME.ARPA$' "$work/argv"
no_mutations
run 0 --check backrest Back Rest /bin/bash "$work/local keytab"
no_mutations

reset_case missing
run 1 --check backrest Back Rest /bin/bash "$work/local keytab"
no_mutations
run 0 backrest Back Rest /bin/bash "$work/local keytab"
grep -q '^ipa-getkeytab -r ' "$work/argv"
[[ $(cat "$work/local keytab") == new-key ]]
[[ $(stat -c %a "$work/local keytab") == 400 ]]

reset_case retrieve-fail
printf 'stale-key\n' > "$work/local keytab"
run 1 backrest Back Rest /bin/bash "$work/local keytab"
[[ $(cat "$work/local keytab") == stale-key ]]
[[ $(grep -c '^ipa-getkeytab ' "$work/argv") == 1 ]]
grep -q '^ipa-getkeytab -r ' "$work/argv"
! grep -q '^install ' "$work/argv"

reset_case new FALSE
run 0 backrest Back Rest /bin/bash "$work/local keytab"
grep -q '^ipa-getkeytab -s ' "$work/argv"
! grep -q '^ipa-getkeytab -r ' "$work/argv"

reset_case unknown UNKNOWN
run 2 backrest Back Rest /bin/bash "$work/local keytab"
no_mutations
reset_case key-auth-fail
run 1 backrest Back Rest /bin/bash "$work/local keytab"
! grep -q '^install ' "$work/argv"

reset_case invalid
run 2 '-invalid' Back Rest /bin/bash
run 2 backrest Back Rest /bin/bash relative.keytab
[[ ! -s $work/argv ]]
printf '%s\n' 'PASS: delegated provisioning errors, argument integrity, key preservation/retrieval, checks, and private cache cleanup'
