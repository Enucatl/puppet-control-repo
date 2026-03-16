#!/usr/bin/env bash

set -euo pipefail

. "$(dirname "$0")/config.sh"

echo "Running configuration inside docker container: $FREEIPA_CONTAINER..."

# We use a heredoc (<<EOF) to run multiple commands inside the single docker exec session
docker exec -i "$FREEIPA_CONTAINER" bash <<EOF
set -eu

echo "Authentication..."
echo "\$PASSWORD" | kinit admin > /dev/null

if [ \$? -ne 0 ]; then
    echo "Authentication failed. Is \\\$PASSWORD set inside the container?"
    exit 1
fi
#ipa service-add nfs/${DOCKER_FQDN}
#ipa service-add nfs/forbearance.${DOMAIN}
ipa service-add-host nfs/${DOCKER_FQDN} --hosts ${DOCKER_FQDN} 2>/dev/null || true
ipa service-add-host nfs/forbearance.${DOMAIN} --hosts forbearance.${DOMAIN} 2>/dev/null || true
ipa service-add nfs/bypaing.${DOMAIN} 2>/dev/null || true
ipa service-add-host nfs/bypaing.${DOMAIN} --hosts bypaing.${DOMAIN} 2>/dev/null || true
ipa service-add nfs/complex.${DOMAIN} 2>/dev/null || true
ipa service-add-host nfs/complex.${DOMAIN} --hosts bypaing.${DOMAIN} 2>/dev/null || true
echo "Configuration Complete."
EOF

ssh "user@${DOCKER_FQDN}" "sudo kinit -k && sudo ipa-getkeytab -s ${FREEIPA_FQDN} -p nfs/${DOCKER_FQDN} -k /etc/krb5.keytab"
ssh "user@forbearance.${DOMAIN}" "sudo kinit -k && sudo ipa-getkeytab -s ${FREEIPA_FQDN} -p nfs/forbearance.${DOMAIN} -k /etc/krb5.keytab"
ssh "user@bypaing.${DOMAIN}" "sudo kinit -k && sudo ipa-getkeytab -s ${FREEIPA_FQDN} -p nfs/bypaing.${DOMAIN} -k /etc/krb5.keytab"
ssh "user@complex.${DOMAIN}" "sudo kinit -k && sudo ipa-getkeytab -s ${FREEIPA_FQDN} -p nfs/complex.${DOMAIN} -k /etc/krb5.keytab"
