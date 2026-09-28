# shellcheck shell=sh
# Shared configuration for infrastructure scripts
# Source this file at the top of each script:
#   Container scripts: . /scripts/config.sh
#   Host scripts:      . "$(dirname "$0")/config.sh"

PUPPET_CONFIG_DIR="${PUPPET_CONFIG_DIR:-$(CDPATH= cd "$(dirname "$0")" && pwd)}"
PUPPET_REPO_ROOT="$(cd "$PUPPET_CONFIG_DIR/../../.." && pwd)"

puppet_allowed_dns_sans_csv() {
  find "$PUPPET_REPO_ROOT/data/nodes" -maxdepth 1 -type f -name '*.yaml' |
    sed 's|.*/||; s/\.yaml$//' |
    sort |
    paste -sd, -
}

# Domain
DOMAIN="home.arpa"
VAULT_FQDN="hcv.${DOMAIN}"
FREEIPA_FQDN="freeipa.${DOMAIN}"
DOCKER_FQDN="docker.${DOMAIN}"

# LDAP
LDAP_BASE_DN="dc=home,dc=arpa"
LDAP_USER_DN="cn=users,cn=accounts,${LDAP_BASE_DN}"
LDAP_GROUP_DN="cn=groups,cn=accounts,${LDAP_BASE_DN}"

# FreeIPA container
FREEIPA_CONTAINER="freeipa"

# PKI TTLs
ROOT_CA_TTL="87600h"        # 10 years
INTERMEDIATE_CA_TTL="43800h" # 5 years
CERT_MAX_TTL="8760h"         # 1 year
VAULT_CERT_TTL="57000h"      # ~6.5 years

# Vault retry defaults
VAULT_RETRIES="${VAULT_RETRIES:-10}"
VAULT_RETRY_INTERVAL="${VAULT_RETRY_INTERVAL:-5}"
