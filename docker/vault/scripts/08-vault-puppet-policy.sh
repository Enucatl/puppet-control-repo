#!/usr/bin/env bash

set -euo pipefail

if [ -f .env ]; then
  set -o allexport
  source .env
  set +o allexport
fi

export VAULT_CACERT=${VAULT_CACERT:-/etc/ssl/certs/ca-certificates.crt}

CERT_AUTH_ACCESSOR=$(vault auth list -format=json | jq -er '."cert/".accessor')
PUPPET_CERTNAME="{{identity.entity.aliases.${CERT_AUTH_ACCESSOR}.name}}"

# The identity comes from the authenticated Puppet certificate, never request data.
vault write pki_int/roles/puppet \
  allowed_domains="$PUPPET_CERTNAME" \
  allowed_domains_template=true \
  allow_bare_domains=true \
  allow_subdomains=true \
  allow_wildcard_certificates=false \
  allow_glob_domains=false \
  allow_any_name=false \
  allow_localhost=false \
  allow_ip_sans=false \
  cn_validations=hostname \
  max_ttl=8760h

# Only the docker.home.arpa identity can reach this role through the ACL below.
vault write pki_int/roles/puppet-docker.home.arpa \
  allowed_domains=docker.home.arpa \
  allow_bare_domains=true \
  allow_subdomains=true \
  allow_wildcard_certificates=true \
  allow_glob_domains=false \
  allow_any_name=false \
  allow_localhost=false \
  allow_ip_sans=false \
  cn_validations=hostname \
  max_ttl=8760h

vault policy write puppet - <<EOF
path "kv/data/puppet" {
    capabilities = ["read"]
}

path "kv/data/wolf" {
    capabilities = ["read"]
}

# Ordinary nodes can issue only within their authenticated hostname.
path "pki_int/issue/puppet" {
    capabilities = ["create", "update"]
}

# The only extra role is puppet-docker.home.arpa, for Docker's wildcards.
path "pki_int/issue/puppet-${PUPPET_CERTNAME}" {
    capabilities = ["create", "update"]
}

path "auth/token/lookup-self" {
  capabilities = ["read"]
}
EOF

# Bootstrap credentials have no runtime secret access and are never granted to
# the Puppet certificate auth role.
vault policy write puppet-enrollment - <<'EOF'
path "auth/token/lookup-self" {
  capabilities = ["read"]
}
EOF
