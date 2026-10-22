#!/usr/bin/env bash
# Check delegated-ticket login without contacting Vault or changing token files.
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir "$work/bin"
export TEST_WORK=$work PATH="$work/bin:$PATH"
export KRB5CCNAME=FILE:/tmp/delegated-ticket VAULT_ADDR=https://vault.home.arpa:8200
unset VAULT_TOKEN
cat > "$work/bin/klist" <<'MOCK'
#!/usr/bin/env bash
[[ "$*" = -s && "$KRB5CCNAME" = FILE:/tmp/delegated-ticket ]]
[[ "${TEST_NO_TICKET:-}" != yes ]]
MOCK
cat > "$work/bin/vault" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
[[ ! -v VAULT_TOKEN ]]
printf '%s\n' "$*" >> "$TEST_WORK/calls"
case "$*" in
  'token lookup') [[ "${TEST_VALID_TOKEN:-}" = yes ]] ;;
  'login -no-print -')
    IFS= read -r token
    [[ "$token" = test-sso-token ]]
    touch "$TEST_WORK/stored" ;;
  *) exit 99 ;;
esac
MOCK
cat > "$work/bin/curl" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
[[ "$1" = --disable && ! -v VAULT_TOKEN ]]
[[ "$KRB5CCNAME" = FILE:/tmp/delegated-ticket ]]
[[ "$*" = *'--negotiate --user :'* ]]
[[ "$*" = *'--request POST https://vault.home.arpa:8200/v1/auth/kerberos/login' ]]
printf 'curl\n' >> "$TEST_WORK/calls"
[[ "${TEST_CURL_FAILURE:-}" != yes ]] || exit 22
printf '%s\n' "${TEST_RESPONSE:-{\"auth\":{\"client_token\":\"test-sso-token\"}}}"
MOCK
chmod +x "$work/bin/"*
script=$repo/modules/profile/files/vault-sso-login
bash -x "$script" > "$work/output" 2>&1
! grep -q test-sso-token "$work/output"
[[ $(cat "$work/calls") = $'token lookup\ncurl\nlogin -no-print -' ]]
[[ -e "$work/stored" ]]
rm "$work/calls" "$work/stored"

TEST_VALID_TOKEN=yes bash "$script"
[[ $(cat "$work/calls") = 'token lookup' ]]
rm "$work/calls"
TEST_NO_TICKET=yes bash "$script"
VAULT_TOKEN=explicit-token bash "$script"
[[ ! -e "$work/calls" ]]

for response in '{}' '{"auth":{"client_token":""}}' '{"auth":{"client_token":123}}' '{"auth":{"client_token":"bad\nvalue"}}' 'invalid-json'; do
  if TEST_RESPONSE=$response bash "$script" >/dev/null 2>&1; then
    printf 'Accepted invalid response: %s\n' "$response" >&2
    exit 1
  fi
  [[ ! -e "$work/stored" ]]
done
if TEST_CURL_FAILURE=yes bash "$script" >/dev/null 2>&1; then
  echo 'Accepted a failed authentication request' >&2
  exit 1
fi
[[ ! -e "$work/stored" ]]

# Sourcing the profile from a noninteractive shell must leave it untouched.
bash -c 'set -e; . "$1"' bash "$repo/modules/profile/files/vault-sso.sh"
env -u VAULT_ADDR bash -c 'set -e; . "$1"; [ "$VAULT_ADDR" = https://vault.home.arpa:8200 ]' bash "$repo/modules/profile/files/vault-sso.sh"
echo 'Vault Kerberos login checks passed.'
