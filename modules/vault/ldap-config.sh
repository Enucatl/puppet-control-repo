BINDPASS=$(vault kv get -mount=secret puppet | grep ldap_ro::password | tr -s ' ' | cut -d ' ' -f 2)

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
