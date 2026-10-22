#!/usr/bin/env bash
# Exercise PAM password handling and user switching without contacting Vault.
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir "$work/bin"
export TEST_WORK=$work
export PATH="$work/bin:$PATH"
export PAM_TYPE=auth PAM_SERVICE=sshd PAM_USER='user@HOME.ARPA'
export VAULT_TOKEN=inherited-token VAULT_LDAP_PASSWORD=inherited-password
cat > "$work/bin/runuser" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
[[ "$1" = -u && "$2" = "$PAM_USER" && "$3" = -- ]]
shift 3
export HOME="$TEST_WORK/user-home"
printf 'switched\n' >> "$TEST_WORK/calls"
exec "$@"
MOCK
cat > "$work/bin/vault" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
[[ "$HOME" = "$TEST_WORK/user-home" && ! -v VAULT_TOKEN ]]
printf '%s\n' "$*" >> "$TEST_WORK/calls"
case "$1 $2" in
  'token lookup')
    [[ ! -v VAULT_LDAP_PASSWORD ]]
    [[ "${TEST_VALID_TOKEN:-}" = yes ]] ;;
  'login -method=ldap')
    [[ "$*" = 'login -method=ldap -no-print username=user@HOME.ARPA' ]]
    [[ "$VAULT_LDAP_PASSWORD" = "$TEST_PASSWORD" ]]
    touch "$TEST_WORK/password-checked"
    [[ "${TEST_LOGIN_FAILURE:-}" != yes ]] ;;
  *) exit 99 ;;
esac
MOCK
chmod +x "$work/bin/"*
script=$repo/modules/profile/templates/vault-pam-login.sh.epp
# Preserve whitespace, backslashes, and newlines from the PAM authentication token.
export TEST_PASSWORD=$' leading\\password\ntrailing '
printf '%s\0' "$TEST_PASSWORD" | bash "$script"
[[ $(cat "$work/calls") = $'switched\ntoken lookup\nlogin -method=ldap -no-print username=user@HOME.ARPA' ]]
[[ -e "$work/password-checked" ]]
rm "$work/calls"
export TEST_VALID_TOKEN=yes
printf '%s\0' "$TEST_PASSWORD" | bash "$script"
[[ $(cat "$work/calls") = $'switched\ntoken lookup' ]]
rm "$work/calls"
unset TEST_VALID_TOKEN
export TEST_LOGIN_FAILURE=yes
printf '%s\0' "$TEST_PASSWORD" | bash "$script"
[[ $(wc -l < "$work/calls") -eq 3 ]]
rm "$work/calls"
printf '\0' | bash "$script"
[[ $(cat "$work/calls") = switched ]]
rm "$work/calls"
export PAM_SERVICE=sudo
printf '%s\0' "$TEST_PASSWORD" | bash "$script"
[[ ! -e "$work/calls" ]]
echo 'Vault PAM login checks passed.'
