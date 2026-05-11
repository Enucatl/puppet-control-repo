#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
. "$REPO_ROOT/docker/vault/scripts/config.sh"

usage() {
  echo "usage: $0 <username> <first-name> <last-name>" >&2
}

if [ "$#" -ne 3 ]; then
  usage
  exit 64
fi

USERNAME="$1"
FIRST_NAME="$2"
LAST_NAME="$3"
GROUP_NAME="wolf-operators"

if ! [[ "$USERNAME" =~ ^[a-z_][a-z0-9_-]*[$]?$ ]]; then
  echo "invalid username: $USERNAME" >&2
  exit 64
fi

if [ -n "${WOLF_OPERATOR_TEMP_PASSWORD:-}" ]; then
  TEMP_PASSWORD="$WOLF_OPERATOR_TEMP_PASSWORD"
else
  TEMP_PASSWORD="$(openssl rand -base64 24)"
fi

docker exec -i \
  -e WOLF_OPERATOR_USERNAME="$USERNAME" \
  -e WOLF_OPERATOR_FIRST_NAME="$FIRST_NAME" \
  -e WOLF_OPERATOR_LAST_NAME="$LAST_NAME" \
  -e WOLF_OPERATOR_GROUP="$GROUP_NAME" \
  -e WOLF_OPERATOR_TEMP_PASSWORD="$TEMP_PASSWORD" \
  "$FREEIPA_CONTAINER" bash <<'EOF'
set -euo pipefail

echo "$PASSWORD" | kinit admin >/dev/null

ipa group-show "$WOLF_OPERATOR_GROUP" >/dev/null 2>&1 \
  || ipa group-add "$WOLF_OPERATOR_GROUP" \
    --desc="Wolf power control operators" >/dev/null

ipa user-show "$WOLF_OPERATOR_USERNAME" >/dev/null 2>&1 \
  || ipa user-add "$WOLF_OPERATOR_USERNAME" \
    --first="$WOLF_OPERATOR_FIRST_NAME" \
    --last="$WOLF_OPERATOR_LAST_NAME" \
    --shell=/usr/sbin/nologin \
    --password-expiration=now >/dev/null

printf '%s\n%s\n' \
  "$WOLF_OPERATOR_TEMP_PASSWORD" \
  "$WOLF_OPERATOR_TEMP_PASSWORD" \
  | ipa passwd "$WOLF_OPERATOR_USERNAME" >/dev/null

ipa user-mod "$WOLF_OPERATOR_USERNAME" \
  --password-expiration=now >/dev/null

ipa group-add-member "$WOLF_OPERATOR_GROUP" \
  --users="$WOLF_OPERATOR_USERNAME" >/dev/null 2>&1 || true
EOF

cat <<EOF
Wolf operator created or updated.
Username: $USERNAME
Temporary password: $TEMP_PASSWORD

The user is a member of $GROUP_NAME only. The password is set to expire
immediately, so the next FreeIPA/Authelia login should require a password
change before normal Wolf access.
EOF
