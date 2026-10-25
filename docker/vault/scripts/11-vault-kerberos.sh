#!/usr/bin/env bash
# Run on an enrolled IPA client with an admin ticket and Vault admin token.
set +x
set -euo pipefail
umask 077

. "$(dirname "$0")/config.sh"
export VAULT_ADDR="${VAULT_ADDR:-https://vault.home.arpa:8200}"
export VAULT_CACERT="${VAULT_CACERT:-/etc/ssl/certs/ca-certificates.crt}"
principal='HTTP/vault.home.arpa@HOME.ARPA'

klist -s || { echo 'A FreeIPA admin Kerberos ticket is required.' >&2; exit 1; }
vault token lookup >/dev/null
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# Read required credentials before changing either service.
vault read -format=json auth/ldap/config > "$work/ldap.json"
vault kv get -field=ldap_ro::password kv/puppet > "$work/bindpass"
test -s "$work/bindpass"
vault read -format=json auth/ldap/groups/admins > "$work/admins.json"

ipa service-show "$principal" --all --raw > "$work/service" 2>/dev/null ||
  ipa service-add "$principal" --skip-host-check --all --raw > "$work/service"

# Permit administrators to recover existing keys without rotating the principal.
if ! grep -Fiq "ipaAllowedToPerform;read_keys: cn=admins,${LDAP_GROUP_DN}" "$work/service"; then
  ipa service-allow-retrieve-keytab "$principal" --groups=admins >/dev/null
fi
# shortcut: omit unused PAC data until Vault ships the gokrb5 SHA2 fix (#493).
flags=$(awk 'tolower($1) == "krbticketflags:" { print $2 }' "$work/service")
flags=${flags:-128}
[[ "$flags" =~ ^[0-9]+$ ]]
if (( (flags & 4194304) == 0 )); then
  ipa service-mod "$principal" --setattr="krbTicketFlags=$((flags | 4194304))" >/dev/null
fi
if grep -Fq 'has_keytab: TRUE' "$work/service"; then
  ipa-getkeytab -r -s "$FREEIPA_FQDN" -p "$principal" -k "$work/vault.keytab"
else
  ipa-getkeytab -s "$FREEIPA_FQDN" -p "$principal" -k "$work/vault.keytab"
fi

vault auth list -format=json > "$work/auth.json"
if ! jq -e 'has("kerberos/")' "$work/auth.json" >/dev/null; then
  vault auth enable kerberos
fi
jq -e '."kerberos/".type == "kerberos"' < <(vault auth list -format=json) >/dev/null
vault auth tune -passthrough-request-headers=Authorization \
  -allowed-response-headers=www-authenticate -default-lease-ttl=8h \
  -max-lease-ttl=24h kerberos/

base64 -w0 "$work/vault.keytab" |
  vault write auth/kerberos/config service_account=HTTP/vault.home.arpa keytab=-

# Pin the Kerberos realm and search FreeIPA's principal attribute, not AD's UPN.
jq --rawfile bindpass "$work/bindpass" --arg userdn "$LDAP_USER_DN" '.data |
  .bindpass = ($bindpass | rtrimstr("\n")) |
  .upndomain = "HOME.ARPA" |
  .userfilter = "(&(krbPrincipalName={{.Username}})(!(uid=tv)))" |
  .groupfilter = "(|(member=uid={{.Username}},\($userdn))(mepManagedBy=uid={{.Username}},\($userdn)))" |
  .tls_min_version = "tls13" | .tls_max_version = "tls13" |
  .token_ttl = "8h" | .token_max_ttl = "24h"' "$work/ldap.json" |
  vault write auth/kerberos/config/ldap -
jq '.data' "$work/admins.json" | vault write auth/kerberos/groups/admins -
echo 'Vault Kerberos SSO is configured for HOME.ARPA.'
