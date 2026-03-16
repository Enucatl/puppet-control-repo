#!/usr/bin/env bash

set -euo pipefail

. "$(dirname "$0")/config.sh"

if [ -f .env ]; then
  set -o allexport
  source .env
  set +o allexport
fi
export VAULT_CACERT=/usr/local/share/ca-certificates/home-arpa/vault_root.crt

curl -s --insecure "$VAULT_ADDR/v1/pki_int/ca_chain" > ~/Downloads/vault_chain.pem
sudo mkdir -p /usr/local/share/ca-certificates/home-arpa
curl --insecure -s "$VAULT_ADDR/v1/pki_int/ca/pem" \
  | sudo tee /usr/local/share/ca-certificates/home-arpa/vault_intermediate.crt > /dev/null
curl --insecure -s "$VAULT_ADDR/v1/pki/ca/pem" \
  | sudo tee /usr/local/share/ca-certificates/home-arpa/vault_root.crt > /dev/null
sudo update-ca-certificates

openssl genrsa -out ~/Downloads/puppet_ca_key.pem 4096
openssl req -new -key ~/Downloads/puppet_ca_key.pem -out ~/Downloads/puppet_ca.csr -subj "/CN=Puppet CA: ${DOCKER_FQDN}"
vault write -format=json pki_int/root/sign-intermediate \
    csr=@"$HOME/Downloads/puppet_ca.csr" \
    format=pem_bundle \
    ttl="$INTERMEDIATE_CA_TTL" \
    common_name="Puppet CA" \
    | jq -r '.data.certificate' > ~/Downloads/puppet_ca_combined.pem
vault read pki/crl/rotate
vault read pki_int/crl/rotate
curl -s "$VAULT_ADDR/v1/pki/crl" -o ~/Downloads/crls.der
curl -s "$VAULT_ADDR/v1/pki_int/crl" -o ~/Downloads/crls_int.der
openssl crl -inform DER -in ~/Downloads/crls.der -out ~/Downloads/crls.pem
openssl crl -inform DER -in ~/Downloads/crls_int.der -out ~/Downloads/crls_int.pem
cat ~/Downloads/crls_int.pem ~/Downloads/crls.pem > ~/Downloads/crls_chain.pem
sudo /opt/puppetlabs/server/bin/puppetserver ca import \
  --cert-bundle ~/Downloads/puppet_ca_combined.pem \
  --crl-chain ~/Downloads/crls_chain.pem \
  --private-key ~/Downloads/puppet_ca_key.pem \
  --subject-alt-names "${DOCKER_FQDN},puppet,docker"
