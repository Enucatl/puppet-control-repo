#!/bin/sh

set -eu

. /scripts/config.sh

# Check if VAULT_ADDR starts with "https"
case "$VAULT_ADDR" in
    https*)
        echo "vault is already configured with HTTPS, nothing to do"
        exit 0
        ;;
esac

# Install jq for JSON processing (Vault image is Alpine based)
# We suppress output to keep logs clean
apk add --no-cache jq > /dev/null 2>&1

KEYS_FILE="/certificates/keys.json"

vault status || true

echo "--- Starting PKI Setup ---"



# 1. Wait for Vault to be active and unsealed
# We loop checking 'vault status'.
# Exit code 0 = Active/Unsealed. Code 2 = Sealed. Code 1 = Error.
echo "Waiting for Vault to be active..."
counter=0
while [ $counter -lt "$VAULT_RETRIES" ]; do
    if vault status > /dev/null 2>&1; then
        echo "Vault is active and unsealed."
        break
    fi
    echo "Vault not ready yet. Retrying..."
    sleep "$VAULT_RETRY_INTERVAL"
    counter=$((counter+1))
done

if [ $counter -eq "$VAULT_RETRIES" ]; then
    echo "Timeout waiting for Vault."
    exit 1
fi

# 2. Login
if [ ! -f "$KEYS_FILE" ]; then
  echo "Keys file not found at $KEYS_FILE"
  exit 1
fi

# Extract root token and login locally
ROOT_TOKEN=$(jq -r ".root_token" "$KEYS_FILE")
export VAULT_TOKEN="$ROOT_TOKEN"

# 3. Idempotency Check: Is PKI enabled?
if vault secrets list -format=json | jq -e '."pki/"' > /dev/null; then
    echo "PKI engine already enabled. Skipping setup."
    exit 0
fi

echo "PKI not found. Initializing infrastructure..."

# 4. Enable and Tune PKI
vault secrets enable pki
vault secrets tune -max-lease-ttl="$ROOT_CA_TTL" pki

# 5. Generate Root CA
# We use -format=json to easily parse the certificate out
echo "Generating Root CA..."
vault write -format=json pki/root/generate/internal \
    common_name="Docker Home Arpa Root CA" \
    ttl="$ROOT_CA_TTL" \
    | jq -r ".data.certificate" > /certificates/ca.crt

# 6. Configure URLs
vault write pki/config/urls \
    issuing_certificates="https://${VAULT_FQDN}:8200/v1/pki/ca" \
    crl_distribution_points="https://${VAULT_FQDN}:8200/v1/pki/crl"

# 7. Create Infra Role
vault write pki/roles/infra-core \
    allowed_domains="$DOMAIN" \
    allow_subdomains=true \
    allow_bare_domains=true \
    allow_localhost=true \
    allow_ip_sans=true \
    require_cn=false \
    max_ttl="$ROOT_CA_TTL"

# 8. Issue Vault Certificate
# This generates the certs that Vault itself will use for TLS
echo "Issuing Vault Server Certificate..."
vault write -format=json pki/issue/infra-core \
    common_name="$VAULT_FQDN" \
    alt_names="${DOCKER_FQDN},${VAULT_FQDN},vault.${DOMAIN}" \
    ip_sans="127.0.0.1" \
    ttl="$VAULT_CERT_TTL" > /tmp/vault_cert_bundle.json

# 9. Export keys to config volume
jq -r ".data.certificate" /tmp/vault_cert_bundle.json > /certificates/vault.crt
jq -r ".data.private_key" /tmp/vault_cert_bundle.json > /certificates/vault.key

# Create Chain (Cert + CA)
cat /certificates/vault.crt /certificates/ca.crt > /certificates/vault_chain.crt

echo "Certificates placed in /certificates/"
echo "--- PKI Setup Complete ---"
