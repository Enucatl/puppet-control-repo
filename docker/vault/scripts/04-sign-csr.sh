#!/bin/sh

set -eu

. /scripts/config.sh

# 1. Install dependencies (Vault image is minimal)
apk add --no-cache jq

# 2. Define paths
CSR_FILE="/data/ipa.csr"
CRT_FILE="/data/ipa.crt"
CA_FILE="/data/ca.crt"

if [ -f "$CA_FILE" ]; then
  echo "FreeIPA CA already initialized, exiting."
  exit 0
fi

# 3. Wait for the CSR to appear
echo "Waiting for FreeIPA to generate CSR at $CSR_FILE..."
while [ ! -f "$CSR_FILE" ]; do
  sleep 5
done

echo "CSR found. Signing with Vault..."

# 2. Login
ROOT_TOKEN=$(jq -r ".root_token" "$KEYS_FILE")
export VAULT_TOKEN="$ROOT_TOKEN"

# 4. Sign the CSR
# Note: FreeIPA acts as an intermediate CA, so we use 'sign-intermediate'
# We ask for 'pem_bundle' to get the cert and the chain
if ! JSON_RESPONSE=$(vault write -format=json pki_int/root/sign-intermediate \
    csr=@"$CSR_FILE" \
    format=pem_bundle \
    ttl="$INTERMEDIATE_CA_TTL" \
    use_csr_values=true \
    exclude_cn_from_sans=true); then
  echo "Error signing certificate with Vault."
  exit 1
fi

# 5. Extract Certificate and CA Chain
# .data.certificate = The signed Intermediate Cert (for FreeIPA)
# .data.issuing_ca  = The CA that signed it (Vault's CA)
echo "$JSON_RESPONSE" | jq -r '.data.certificate' > "$CRT_FILE"
echo "$JSON_RESPONSE" | jq -r '.data.issuing_ca' > "$CA_FILE"

# If you have a longer chain (Root -> Intermediate -> FreeIPA),
# you might need to bundle the whole chain into ca.crt:
# echo "$JSON_RESPONSE" | jq -r '.data.ca_chain[]' >> "$CA_FILE"

echo "Certificates generated:"
ls -l "$CRT_FILE" "$CA_FILE"

echo "Done. You may now restart the FreeIPA container to complete installation."
