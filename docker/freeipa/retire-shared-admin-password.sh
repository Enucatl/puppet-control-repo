#!/usr/bin/env bash
# Run after deploying and verifying delegated provisioning. Requires admin Vault
# access. Replacement passwords are saved before either account is changed, so
# a rerun can recover from an interrupted rotation without losing credentials.
set +x
set -euo pipefail
export LC_ALL=C
umask 077
[[ $EUID == 0 && $(hostname -f) == docker.home.arpa ]] || {
  echo 'Run as root on docker.home.arpa.' >&2
  exit 1
}
work=$(mktemp -d)
trap 'kdestroy 2>/dev/null || :; rm -rf -- "$work"' EXIT
export KRB5CCNAME="FILE:$work/ccache"

/usr/local/sbin/puppet-ipa-provision-user --check backrest Backrest Backup /usr/sbin/nologin /etc/krb5-backrest.keytab

# Use the ordinary agent certificate, not the administrative Vault token, to
# verify the new path is inaccessible to Puppet before storing anything there.
vault login -method=cert -token-only -no-store \
  -client-cert=/etc/puppetlabs/puppet/ssl/certs/docker.home.arpa.pem \
  -client-key=/etc/puppetlabs/puppet/ssl/private_keys/docker.home.arpa.pem \
  >"$work/puppet-token"
[[ $(VAULT_TOKEN=$(cat "$work/puppet-token") vault token capabilities kv/data/freeipa-admin) == deny ]]
if VAULT_TOKEN=$(cat "$work/puppet-token") vault kv get kv/freeipa-admin > /dev/null 2>"$work/denied"; then
  echo 'Puppet can read kv/freeipa-admin; refusing rotation.' >&2
  exit 1
fi
grep -q 'Code: 403' "$work/denied"

vault kv get -format=json kv/puppet >"$work/shared"
if ! vault kv get -format=json kv/freeipa-admin >"$work/admin" 2>"$work/error"; then
  grep -q '^No value found at kv/data/freeipa-admin$' "$work/error" || { cat "$work/error" >&2; exit 1; }
  openssl rand -base64 36 >"$work/new-admin"
  openssl rand -base64 36 >"$work/new-dm"
  jq -n --rawfile admin "$work/new-admin" --rawfile dm "$work/new-dm" \
    '{admin_password: ($admin|rtrimstr("\n")), directory_manager_password: ($dm|rtrimstr("\n"))}' >"$work/passwords"
  vault kv put -cas=0 kv/freeipa-admin @"$work/passwords" >/dev/null
  vault kv get -format=json kv/freeipa-admin >"$work/admin"
fi
jq -er '.data.data.admin_password | select(type == "string" and length > 20)' "$work/admin" >"$work/new-admin"
jq -ej '.data.data.directory_manager_password | select(type == "string" and length > 20)' "$work/admin" >"$work/new-dm"
jq -e '.data.data.admin_password != .data.data.directory_manager_password' "$work/admin" >/dev/null

# Verify a completed step first; otherwise authenticate with the old credential
# and rotate just that account. Passwords only travel through stdin/private files.
if ! kinit admin <"$work/new-admin" > /dev/null 2>"$work/error"; then
  jq -er '.data.data["freeipa_users::admin_password"]' "$work/shared" >"$work/old-admin"
  kinit admin <"$work/old-admin" >/dev/null
  { cat "$work/old-admin"; cat "$work/new-admin"; cat "$work/new-admin"; } | kpasswd admin >/dev/null
  kdestroy
  kinit admin <"$work/new-admin" >/dev/null
fi
ipa user-show admin >/dev/null
echo 'Replacement administrator password verified.'

if ! ldapwhoami -x -H ldaps://freeipa.home.arpa -D 'cn=Directory Manager' \
  -y "$work/new-dm" >/dev/null 2>"$work/error"; then
  jq -ej '.data.data["freeipa::install::server::options::ds-password"]' "$work/shared" >"$work/old-dm"
  ldapwhoami -x -H ldaps://freeipa.home.arpa -D 'cn=Directory Manager' -y "$work/old-dm" >/dev/null
  {
    printf 'dn: cn=config\nchangetype: modify\nreplace: nsslapd-rootpw\nnsslapd-rootpw:: '
    base64 -w0 <"$work/new-dm"
    printf '\n'
  } | ldapmodify -x -H ldaps://freeipa.home.arpa -D 'cn=Directory Manager' -y "$work/old-dm" >/dev/null
  ldapwhoami -x -H ldaps://freeipa.home.arpa -D 'cn=Directory Manager' -y "$work/new-dm" >/dev/null
fi
echo 'Replacement Directory Manager password verified.'

# The initialized data volume needs no installation password. Remove the old
# bootstrap environment value from both its source and the running container.
repo=$(cd "$(dirname "$0")/../.." && pwd)
sed -i '/^PASSWORD=/d' "$repo/.env"
if docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' freeipa | grep -q '^PASSWORD='; then
  docker compose --env-file /opt/docker/.env --env-file "$repo/.env" \
    -f "$repo/docker/docker-compose.yml" up -d --no-deps --force-recreate --wait --wait-timeout 240 freeipa
fi
kdestroy
kinit admin <"$work/new-admin" >/dev/null
ipa user-show admin >/dev/null
ldapwhoami -x -H ldaps://freeipa.home.arpa -D 'cn=Directory Manager' -y "$work/new-dm" >/dev/null

# Remove exactly four fields, using the version read above. A concurrent writer
# causes a CAS failure, so no unrelated data can be silently overwritten.
jq '.data.data | del(.["freeipa_users::admin_password"],
  .["freeipa::install::client::options::enrollment-password"],
  .["freeipa::install::server::options::admin-password"],
  .["freeipa::install::server::options::ds-password"])' "$work/shared" >"$work/remaining"
vault kv put -cas="$(jq -r '.data.metadata.version' "$work/shared")" kv/puppet @"$work/remaining" >/dev/null
vault kv get -format=json kv/puppet | jq -S '.data.data' >"$work/current"
jq -S . "$work/remaining" >"$work/expected"
cmp "$work/expected" "$work/current"
jq -e 'has("freeipa::client::password")' "$work/current" >/dev/null
echo 'Four shared fields removed; all unrelated values and delegated enrollment retained.'
