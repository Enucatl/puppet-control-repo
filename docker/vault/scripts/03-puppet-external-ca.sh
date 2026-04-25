#!/usr/bin/env bash

set -euo pipefail

. "$(dirname "$0")/config.sh"

if [ -f .env ]; then
  set -o allexport
  source .env
  set +o allexport
fi
export VAULT_CACERT=/usr/local/share/ca-certificates/home-arpa/vault_root.crt

PUPPET_CA_COMMON_NAME="${PUPPET_CA_COMMON_NAME:-Puppet_CA}"
PUPPET_ENCODING_TEST_CERTNAME="${PUPPET_ENCODING_TEST_CERTNAME:-puppet-ca-encoding-test.${DOMAIN}}"
PUPPETSERVER_BIN="${PUPPETSERVER_BIN:-/opt/puppetlabs/server/bin/puppetserver}"
PUPPETSERVER_CA_DIR="${PUPPETSERVER_CA_DIR:-/etc/puppetlabs/puppetserver/ca}"
PUPPET_RUBY="${PUPPET_RUBY:-/opt/puppetlabs/puppet/bin/ruby}"
PUPPET_SSL_DIR="${PUPPET_SSL_DIR:-/etc/puppetlabs/puppet/ssl}"
PUPPET_CERT_AUTH_ROLE="${PUPPET_CERT_AUTH_ROLE:-puppet}"
RESTART_PUPPETSERVER_AFTER_IMPORT="${RESTART_PUPPETSERVER_AFTER_IMPORT:-true}"
BACKUP_PUPPET_SSL_DIR="${BACKUP_PUPPET_SSL_DIR:-true}"
BACKUP_PUPPETSERVER_CA_DIR="${BACKUP_PUPPETSERVER_CA_DIR:-true}"
PUPPET_SSL_BACKUP_FILE="${PUPPET_SSL_BACKUP_FILE:-$HOME/Downloads/puppet-ssl-$(date -u +%Y%m%dT%H%M%SZ).tar.gz}"
PUPPETSERVER_CA_BACKUP_FILE="${PUPPETSERVER_CA_BACKUP_FILE:-$HOME/Downloads/puppetserver-ca-$(date -u +%Y%m%dT%H%M%SZ).tar.gz}"

cleanup_encoding_test_cert() {
  sudo "$PUPPETSERVER_BIN" ca clean --certname "$PUPPET_ENCODING_TEST_CERTNAME" >/dev/null 2>&1 || true
}

backup_puppet_ssl_dir() {
  if [ "$BACKUP_PUPPET_SSL_DIR" != "true" ]; then
    return 0
  fi

  if [ ! -d "$PUPPET_SSL_DIR" ]; then
    echo "Puppet SSL directory not found: $PUPPET_SSL_DIR" >&2
    return 1
  fi

  mkdir -p "$(dirname "$PUPPET_SSL_BACKUP_FILE")"
  sudo tar -C "$(dirname "$PUPPET_SSL_DIR")" -czf "$PUPPET_SSL_BACKUP_FILE" "$(basename "$PUPPET_SSL_DIR")"
  sudo chown "$(id -u):$(id -g)" "$PUPPET_SSL_BACKUP_FILE"
  sudo rm -rf "$PUPPET_SSL_DIR"
  echo "Backed up and cleared Puppet SSL directory to $PUPPET_SSL_BACKUP_FILE"
}

backup_puppetserver_ca_dir() {
  if [ "$BACKUP_PUPPETSERVER_CA_DIR" != "true" ]; then
    return 0
  fi

  if ! sudo test -d "$PUPPETSERVER_CA_DIR"; then
    echo "Puppet Server CA directory not found: $PUPPETSERVER_CA_DIR" >&2
    return 1
  fi

  mkdir -p "$(dirname "$PUPPETSERVER_CA_BACKUP_FILE")"
  sudo tar -C "$(dirname "$PUPPETSERVER_CA_DIR")" -czf "$PUPPETSERVER_CA_BACKUP_FILE" "$(basename "$PUPPETSERVER_CA_DIR")"
  sudo chown "$(id -u):$(id -g)" "$PUPPETSERVER_CA_BACKUP_FILE"
  sudo rm -rf "$PUPPETSERVER_CA_DIR"
  echo "Backed up and cleared Puppet Server CA directory to $PUPPETSERVER_CA_BACKUP_FILE"
}

verify_puppet_ca_issuer_encoding() {
  local certname="$1"
  local ca_cert="${PUPPET_SSL_DIR}/certs/ca.pem"
  local leaf_cert="${PUPPETSERVER_CA_DIR}/signed/${certname}.pem"

  if [ ! -f "$ca_cert" ]; then
    echo "Puppet CA certificate not found: $ca_cert" >&2
    return 1
  fi

  if ! sudo test -f "$leaf_cert"; then
    echo "Generated test certificate not found: $leaf_cert" >&2
    return 1
  fi

  sudo "$PUPPET_RUBY" -ropenssl -e '
    ca = OpenSSL::X509::Certificate.new(File.read(ARGV[0]))
    leaf = OpenSSL::X509::Certificate.new(File.read(ARGV[1]))

    ca_subject = ca.subject.to_der
    leaf_issuer = leaf.issuer.to_der

    puts "Puppet CA subject: #{ca.subject.to_s(OpenSSL::X509::Name::RFC2253)}"
    puts "Test leaf issuer: #{leaf.issuer.to_s(OpenSSL::X509::Name::RFC2253)}"
    puts "Puppet CA subject DER: #{ca_subject.unpack1("H*")}"
    puts "Test leaf issuer DER: #{leaf_issuer.unpack1("H*")}"

    if ca_subject != leaf_issuer
      warn "Puppet CA subject DER does not match generated leaf issuer DER"
      exit 1
    end
  ' "$ca_cert" "$leaf_cert"
}

update_vault_puppet_cert_auth() {
  local ca_cert="${PUPPET_SSL_DIR}/certs/ca.pem"

  if [ ! -f "$ca_cert" ]; then
    echo "Puppet CA certificate not found: $ca_cert" >&2
    return 1
  fi

  vault auth list 2>/dev/null | grep -q '^cert/' || vault auth enable cert

  vault write "auth/cert/certs/${PUPPET_CERT_AUTH_ROLE}" \
    certificate=@"$ca_cert" \
    token_policies="puppet" \
    allowed_dns_sans="*.${DOMAIN}" \
    token_ttl=15m
}

verify_vault_puppet_cert_auth() {
  local certname="$1"
  local cert_file="${PUPPET_SSL_DIR}/certs/${certname}.pem"
  local key_file="${PUPPET_SSL_DIR}/private_keys/${certname}.pem"

  if ! sudo test -f "$cert_file"; then
    echo "Generated test certificate not found: $cert_file" >&2
    return 1
  fi

  if ! sudo test -f "$key_file"; then
    echo "Generated test private key not found: $key_file" >&2
    return 1
  fi

  sudo env \
    VAULT_ADDR="$VAULT_ADDR" \
    VAULT_CACERT="$VAULT_CACERT" \
    VAULT_CLIENT_CERT="$cert_file" \
    VAULT_CLIENT_KEY="$key_file" \
    vault login -method=cert -no-store "name=${PUPPET_CERT_AUTH_ROLE}" >/dev/null
}

curl -s --insecure "$VAULT_ADDR/v1/pki_int/ca_chain" > ~/Downloads/vault_chain.pem
sudo mkdir -p /usr/local/share/ca-certificates/home-arpa
curl --insecure -s "$VAULT_ADDR/v1/pki_int/ca/pem" \
  | sudo tee /usr/local/share/ca-certificates/home-arpa/vault_intermediate.crt > /dev/null
curl --insecure -s "$VAULT_ADDR/v1/pki/ca/pem" \
  | sudo tee /usr/local/share/ca-certificates/home-arpa/vault_root.crt > /dev/null
sudo update-ca-certificates

openssl genrsa -out ~/Downloads/puppet_ca_key.pem 4096
openssl req -new -key ~/Downloads/puppet_ca_key.pem -out ~/Downloads/puppet_ca.csr -subj "/CN=${PUPPET_CA_COMMON_NAME}"
vault write -format=json pki_int/root/sign-intermediate \
    csr=@"$HOME/Downloads/puppet_ca.csr" \
    format=pem_bundle \
    ttl="$INTERMEDIATE_CA_TTL" \
    common_name="$PUPPET_CA_COMMON_NAME" \
    | jq -r '.data.certificate' > ~/Downloads/puppet_ca_combined.pem
vault read pki/crl/rotate
vault read pki_int/crl/rotate
curl -s "$VAULT_ADDR/v1/pki/crl" -o ~/Downloads/crls.der
curl -s "$VAULT_ADDR/v1/pki_int/crl" -o ~/Downloads/crls_int.der
openssl crl -inform DER -in ~/Downloads/crls.der -out ~/Downloads/crls.pem
openssl crl -inform DER -in ~/Downloads/crls_int.der -out ~/Downloads/crls_int.pem
cat ~/Downloads/crls_int.pem ~/Downloads/crls.pem > ~/Downloads/crls_chain.pem

backup_puppet_ssl_dir
backup_puppetserver_ca_dir
sudo "$PUPPETSERVER_BIN" ca import \
  --cert-bundle ~/Downloads/puppet_ca_combined.pem \
  --crl-chain ~/Downloads/crls_chain.pem \
  --private-key ~/Downloads/puppet_ca_key.pem \
  --subject-alt-names "${DOCKER_FQDN},puppet,docker"

if [ "$RESTART_PUPPETSERVER_AFTER_IMPORT" = "true" ]; then
  sudo systemctl restart puppetserver
fi

cleanup_encoding_test_cert
trap cleanup_encoding_test_cert EXIT
sudo "$PUPPETSERVER_BIN" ca generate --certname "$PUPPET_ENCODING_TEST_CERTNAME"
if ! verify_puppet_ca_issuer_encoding "$PUPPET_ENCODING_TEST_CERTNAME"; then
  exit 1
fi

update_vault_puppet_cert_auth
verify_vault_puppet_cert_auth "$PUPPET_ENCODING_TEST_CERTNAME"
cleanup_encoding_test_cert
trap - EXIT
