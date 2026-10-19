#!/bin/bash
set -euo pipefail
# Shared Puppet enrollment script
# Usage: configure-puppet.sh <node_type> <vault_token> <certname> [puppet_server]
# Read-only host preflight: configure-puppet.sh --preflight <certname>
# Resume a catalog: configure-puppet.sh --apply <certname> <puppet_server>

check_existing_identity() {
  local host_key
  host_key=$(/opt/puppetlabs/bin/puppet config print hostprivkey --section agent "$@")
  if [[ -s $host_key ]]; then
    /opt/puppetlabs/puppet/bin/ruby -ropenssl -e '
      key = OpenSSL::PKey.read(File.read(ARGV.fetch(0)))
      oid = OpenSSL::ASN1.decode(key.public_to_der).value[0].value[0].oid
      abort "Existing identity is not ML-DSA-44; use the explicit identity migration procedure" unless oid == "2.16.840.1.101.3.4.3.17"
    ' "$host_key"
  fi
}

apply_catalog() {
  systemctl unmask --runtime puppet.service
  systemctl enable puppet
  /opt/puppetlabs/bin/puppet resource service puppet enable=true
  local puppet_status=0
  /opt/puppetlabs/bin/puppet agent --test --waitforlock 300 || puppet_status=$?
  case "$puppet_status" in
    0|2) ;;
    *) return "$puppet_status" ;;
  esac
  /opt/puppetlabs/bin/puppet resource service puppet ensure=running
}

if [[ ${1:-} == --preflight ]]; then
  check_existing_identity
  check_existing_identity --certname "${2:?certname required}"
  exit 0
fi

if [[ ${1:-} == --apply ]]; then
  export OPENSSL_CONF=/etc/puppetlabs/puppet/openssl.cnf
  test -s "$OPENSSL_CONF"
  [[ $(/opt/puppetlabs/bin/puppet config print certname --section agent) == "${2:?certname required}" &&
     $(/opt/puppetlabs/bin/puppet config print server --section agent) == "${3:?server required}" ]]
  test -s "$(/opt/puppetlabs/bin/puppet config print hostcert --section agent)"
  check_existing_identity
  systemctl stop puppet
  apply_catalog
  exit
fi

NODE_TYPE="${1:?node_type required}"
VAULT_TOKEN="${2:?vault_token required}"
if [[ $VAULT_TOKEN == - ]]; then
  IFS= read -r VAULT_TOKEN
  [[ -n $VAULT_TOKEN ]] || { echo 'Missing enrollment token on stdin.' >&2; exit 1; }
fi
CERTNAME="${3:?certname required}"
PUPPET_SERVER="${4:-docker.home.arpa}"

export OPENSSL_CONF=/etc/puppetlabs/puppet/openssl.cnf
test -s "$OPENSSL_CONF"
# The new trust bundle is supplied through authenticated provisioning/restore.
test -s /etc/puppetlabs/puppet/ssl/certs/ca.pem || {
  echo 'Install authenticated OpenVox CA trust before enrollment.' >&2; exit 1;
}
check_existing_identity --certname "$CERTNAME"
systemctl stop puppet
/opt/puppetlabs/bin/puppet config set key_type mldsa44 --section main
/opt/puppetlabs/bin/puppet config set key_type mldsa44 --section agent
test "$(/opt/puppetlabs/bin/puppet config print key_type --section agent)" = mldsa44

# CSR Attributes (for autosigning)
mkdir -p /etc/puppetlabs/puppet/
install -m 0640 /dev/null /etc/puppetlabs/puppet/csr_attributes.yaml
printf '%s' "$VAULT_TOKEN" | /opt/puppetlabs/puppet/bin/ruby -rjson -e '
  puts JSON.generate({"custom_attributes" => {"1.2.840.113549.1.9.7" => STDIN.read}})
' > /etc/puppetlabs/puppet/csr_attributes.yaml

# Custom Facts
mkdir -p /etc/facter/facts.d/
echo "node_type=${NODE_TYPE}" > /etc/facter/facts.d/node_type.txt

# Puppet Configuration
/opt/puppetlabs/bin/puppet config set server "$PUPPET_SERVER" --section main
/opt/puppetlabs/bin/puppet config set certname "$CERTNAME" --section main
/opt/puppetlabs/bin/puppet config set certificate_revocation leaf --section agent

apply_catalog
