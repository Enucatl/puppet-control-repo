#!/usr/bin/env bash

set -euo pipefail

. "$(dirname "$0")/config.sh"

echo "Running configuration inside docker container: $FREEIPA_CONTAINER..."

# We use a heredoc (<<EOF) to run multiple commands inside the single docker exec session
docker exec -i "$FREEIPA_CONTAINER" bash <<EOF
set -eu

# 1. Authenticate using the internal environment variable
echo "Authentication..."
echo "\$PASSWORD" | kinit admin > /dev/null

if [ \$? -ne 0 ]; then
    echo "Authentication failed. Is \\\$PASSWORD set inside the container?"
    exit 1
fi

ipa user-show ldap_ro 2>/dev/null || ipa user-add ldap_ro --first ldap --last ro --shell /usr/sbin/nologin
ipa user-show printer 2>/dev/null || ipa user-add printer --first printer --last printer --shell /usr/sbin/nologin
ipa user-show airflow 2>/dev/null || ipa user-add airflow --first airflow --last airflow --shell /usr/sbin/nologin

echo "Configuration Complete."
EOF
