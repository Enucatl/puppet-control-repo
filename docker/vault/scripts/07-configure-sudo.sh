#!/usr/bin/env bash

set -euo pipefail

. "$(dirname "$0")/config.sh"

# Configuration
GROUP_NAME="admins"
TARGET_USER="user"
RULE_NAME="admins_sudo_rule"

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

ipa config-mod --defaultshell=/bin/bash
ipa pwpolicy-mod --maxlife=20000

TEMP_PASSWORD=\$(openssl rand -base64 16)

ipa user-show ${TARGET_USER} 2>/dev/null || ipa user-add ${TARGET_USER} \
    --first="${TARGET_USER}" \
    --last="${TARGET_USER}" \
    --sshpubkey="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEot3u2cV0DaYXoTiFLyCQkEGixSVZhdzddnhbRtaPu/ 1423701+Enucatl@users.noreply.github.com"

echo "\${TEMP_PASSWORD}" | ipa passwd ${TARGET_USER} || true
echo "Temporary password set."
echo "\${TEMP_PASSWORD}"

# 3. Add user '${TARGET_USER}' to the group
echo "Adding user '${TARGET_USER}' to '${GROUP_NAME}'..."
ipa group-add-member ${GROUP_NAME} --users=${TARGET_USER} || echo "   (User likely already in group)"

# 4. Create the Sudo Rule
echo "Creating Sudo Rule: ${RULE_NAME}..."
ipa sudorule-add ${RULE_NAME} 2>/dev/null || true

# 5. Configure the Rule Details
echo "Configuring Rule details..."

# Apply to the group
ipa sudorule-add-user ${RULE_NAME} --groups=${GROUP_NAME} 2>/dev/null || true

# Allow on ALL hosts
ipa sudorule-mod ${RULE_NAME} --hostcat=all

# Allow ALL commands
ipa sudorule-mod ${RULE_NAME} --cmdcat=all

# Allow to RunAs ALL users (root, etc)
ipa sudorule-mod ${RULE_NAME} --runasusercat=all

# Note: We do NOT add '!authenticate'.
# By default, FreeIPA sudo rules REQUIRE a password.
# You only add an option if you want to turn that off.

echo "Configuration Complete."
EOF
