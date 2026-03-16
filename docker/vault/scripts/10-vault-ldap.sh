#!/usr/bin/env bash

set -euo pipefail

. "$(dirname "$0")/config.sh"

if [ -f .env ]; then
  set -o allexport
  source .env
  set +o allexport
fi
export VAULT_CACERT=/etc/ssl/certs/ca-certificates.crt

vault auth list 2>/dev/null | grep -q '^ldap/' || vault auth enable ldap

LDAP_BIND_PASSWORD=$(vault kv get -field=ldap_ro::password kv/puppet)
export LDAP_BIND_PASSWORD

# Check if the password was retrieved successfully (optional)
if [ -z "$LDAP_BIND_PASSWORD" ]; then
    echo "Error: Could not retrieve LDAP bind password from Vault KV."
    exit 1
fi

echo "LDAP bind password retrieved successfully."

vault write auth/ldap/config \
    url="ldaps://${FREEIPA_FQDN}" \
    binddn="uid=ldap_ro,${LDAP_USER_DN}" \
    bindpass="${LDAP_BIND_PASSWORD}" \
    userdn="${LDAP_USER_DN}" \
    userattr="uid" \
    userfilter="(&({{.UserAttr}}={{.Username}})(objectClass=person))" \
    groupattr="cn" \
    groupdn="${LDAP_GROUP_DN}" \
    groupfilter="(|(member={{.UserDN}})(mepManagedBy={{.UserDN}}))" \
    tls_server_name="${FREEIPA_FQDN}" \
    starttls="false" \
    request_timeout="10s" \
    certificate=@/etc/ssl/certs/ca-certificates.crt

unset LDAP_BIND_PASSWORD
