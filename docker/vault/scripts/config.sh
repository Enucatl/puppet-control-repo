# shellcheck shell=sh
# Shared configuration for infrastructure scripts
# Source this file at the top of each script:
#   Container scripts: . /scripts/config.sh
#   Host scripts:      . "$(dirname "$0")/config.sh"

# Domain
DOMAIN="home.arpa"
FREEIPA_FQDN="freeipa.${DOMAIN}"

# LDAP
LDAP_BASE_DN="dc=home,dc=arpa"
LDAP_USER_DN="cn=users,cn=accounts,${LDAP_BASE_DN}"
LDAP_GROUP_DN="cn=groups,cn=accounts,${LDAP_BASE_DN}"

# FreeIPA container
FREEIPA_CONTAINER="freeipa"
