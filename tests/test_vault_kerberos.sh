#!/usr/bin/env bash
# Configure Kerberos against mocked IPA and Vault; never change live credentials.
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir "$work/bin"
export TEST_WORK=$work PATH="$work/bin:$PATH"
cat > "$work/bin/klist" <<'MOCK'
#!/usr/bin/env bash
[[ "$*" = -s && "${TEST_CASE:-}" != no-ticket ]]
MOCK
cat > "$work/bin/ipa" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
printf 'ipa %s\n' "$*" >> "$TEST_WORK/calls"
[[ "$2" = HTTP/vault.home.arpa@HOME.ARPA ]]
case "$1" in
  service-show)
    [[ "${TEST_CASE:-}" != new ]] || exit 2
    echo 'has_keytab: TRUE'
    if [[ "${TEST_CASE:-}" = permitted ]]; then
      echo 'ipaAllowedToPerform;read_keys: cn=admins,cn=groups,cn=accounts,dc=home,dc=arpa'
      echo 'krbTicketFlags: 4194688'
    elif [[ "${TEST_CASE:-}" = existing ]]; then
      echo 'krbTicketFlags: 384'
    elif [[ "${TEST_CASE:-}" = bad-flags ]]; then
      echo 'krbTicketFlags: invalid'
    fi ;;
  service-add) [[ "$*" = *'--skip-host-check --all --raw' ]] ;;
  service-allow-retrieve-keytab) [[ "$3" = --groups=admins ]] ;;
  service-mod)
    [[ "$#" = 3 ]]
    if [[ "${TEST_CASE:-}" = existing ]]; then
      [[ "$3" = --setattr=krbTicketFlags=4194688 ]]
    else
      [[ "$3" = --setattr=krbTicketFlags=4194432 ]]
    fi ;;
  *) exit 99 ;;
esac
MOCK
cat > "$work/bin/ipa-getkeytab" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
printf 'ipa-getkeytab %s\n' "$*" >> "$TEST_WORK/calls"
if [[ "${TEST_CASE:-}" = new ]]; then
  [[ "$1" = -s ]]
else
  [[ "$1" = -r ]]
  shift
fi
[[ "$1" = -s && "$2" = ipa.home.arpa && "$3" = -p && "$4" = HTTP/vault.home.arpa@HOME.ARPA && "$5" = -k ]]
[[ "${TEST_CASE:-}" != keytab-fail ]] || exit 1
printf 'test-secret-keytab' > "$6"
MOCK
cat > "$work/bin/vault" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
printf 'vault %s\n' "$*" >> "$TEST_WORK/calls"
case "$*" in
  'token lookup') [[ "${TEST_CASE:-}" != token-fail ]] ;;
  'read -format=json auth/ldap/config')
    echo '{"data":{"url":"ldaps://ipa.home.arpa","binddn":"uid=ldap_ro","userdn":"cn=users","groupdn":"cn=groups","userattr":"uid","groupfilter":"(member={{.UserDN}})","bindpass":"redacted","upndomain":"old","token_ttl":0}}' ;;
  'kv get -field=ldap_ro::password kv/puppet')
    [[ "${TEST_CASE:-}" != secret-fail ]] || exit 1
    echo 'test-secret-bindpass' ;;
  'read -format=json auth/ldap/groups/admins') echo '{"data":{"policies":["admin"]}}' ;;
  'auth list -format=json')
    if [[ "${TEST_CASE:-}" = new && ! -e "$TEST_WORK/enabled" ]]; then echo '{}'; else echo '{"kerberos/":{"type":"kerberos"}}'; fi ;;
  'auth enable kerberos') touch "$TEST_WORK/enabled" ;;
  'auth tune -passthrough-request-headers=Authorization -allowed-response-headers=www-authenticate -default-lease-ttl=8h -max-lease-ttl=24h kerberos/') ;;
  'write auth/kerberos/config service_account=HTTP/vault.home.arpa keytab=-')
    [[ "$(cat)" = "$(printf 'test-secret-keytab' | base64 -w0)" ]]
    touch "$TEST_WORK/keytab-written" ;;
  'write auth/kerberos/config/ldap -')
    jq -e '.url == "ldaps://ipa.home.arpa" and .binddn == "uid=ldap_ro" and .userdn == "cn=users" and .groupdn == "cn=groups" and .userattr == "uid" and .groupfilter == "(|(member=uid={{.Username}},cn=users,cn=accounts,dc=home,dc=arpa)(mepManagedBy=uid={{.Username}},cn=users,cn=accounts,dc=home,dc=arpa))" and .bindpass == "test-secret-bindpass" and .upndomain == "HOME.ARPA" and .userfilter == "(krbPrincipalName={{.Username}})" and .tls_min_version == "tls13" and .tls_max_version == "tls13" and .token_ttl == "8h" and .token_max_ttl == "24h"' >/dev/null
    touch "$TEST_WORK/ldap-written" ;;
  'write auth/kerberos/groups/admins -')
    jq -e '.policies == ["admin"]' >/dev/null
    touch "$TEST_WORK/admins-written" ;;
  *) exit 99 ;;
esac
MOCK
chmod +x "$work/bin/"*
script=$repo/docker/vault/scripts/11-vault-kerberos.sh
for scenario in new existing permitted; do
  rm -f "$work/calls" "$work/enabled" "$work/"*-written
  TEST_CASE=$scenario bash "$script" >/dev/null
  [[ -e "$work/keytab-written" && -e "$work/ldap-written" && -e "$work/admins-written" ]]
  ! grep -q 'test-secret' "$work/calls"
  if [[ "$scenario" = new ]]; then
    grep -q '^ipa service-add ' "$work/calls"
    grep -q '^vault auth enable kerberos$' "$work/calls"
    ! grep -q '^ipa-getkeytab -r ' "$work/calls"
  else
    ! grep -q '^ipa service-add ' "$work/calls"
    ! grep -q '^vault auth enable ' "$work/calls"
    grep -q '^ipa-getkeytab -r ' "$work/calls"
  fi
  if [[ "$scenario" = permitted ]]; then
    ! grep -q '^ipa service-allow-retrieve-keytab ' "$work/calls"
    ! grep -q '^ipa service-mod ' "$work/calls"
  else
    grep -q '^ipa service-allow-retrieve-keytab ' "$work/calls"
    grep -q '^ipa service-mod HTTP/vault.home.arpa@HOME.ARPA --setattr=krbTicketFlags=' "$work/calls"
  fi
done
for scenario in no-ticket token-fail secret-fail keytab-fail bad-flags; do
  rm -f "$work/calls" "$work/"*-written
  if TEST_CASE=$scenario bash "$script" >/dev/null 2>&1; then
    echo "Accepted failure: $scenario" >&2
    exit 1
  fi
  [[ ! -e "$work/keytab-written" && ! -e "$work/ldap-written" && ! -e "$work/admins-written" ]]
  if [[ "$scenario" = no-ticket ]]; then
    [[ ! -e "$work/calls" ]]
  fi
done
echo 'Vault Kerberos setup checks passed.'
