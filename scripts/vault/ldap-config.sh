#!/bin/bash
# Purpose: Configure LDAP authentication in HashiCorp Vault using the vault CLI.
# Dependencies: Requires HashiCorp Vault CLI and access to Vault server.

# Retrieve the LDAP bind password from HashiCorp Vault.
BINDPASS=$(vault kv get -mount=secret -field ldap_ro::password puppet)

# Configure LDAP authentication in HashiCorp Vault.
vault write auth/ldap/config \
    url="ldaps://ipa.home.arpa" \
    userattr="krbPrincipalName" \
    userdn="cn=users,cn=accounts,dc=home,dc=arpa" \
    groupdn="cn=users,cn=accounts,dc=home,dc=arpa" \
    groupfilter="(&(objectClass=person)(krbPrincipalName={{.Username}}))" \
    groupattr="memberOf" \
    binddn="uid=ldap_ro,cn=sysaccounts,cn=etc,dc=home,dc=arpa" \
    bindpass="${BINDPASS}" \
    certificate=@/etc/ssl/certs/root_2022_ca.pem \
    insecure_tls=false \
    starttls=false

# Exit with the status of the last executed command.
exit $?
