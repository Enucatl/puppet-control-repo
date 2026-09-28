#!/usr/bin/env bash

set +x
set -euo pipefail

ROOT_TOKEN_FILE=${ROOT_TOKEN_FILE:-/bootstrap/root-token}

# Run last, using a separately authenticated administrator session.
admin=$(vault token lookup -format=json)
jq -e '.data | ((.policies + (.identity_policies // [])) | index("admin") != null)
    and (.policies | index("root") == null) and .orphan == true' <<< "$admin" >/dev/null || {
    echo 'Log in with an independent, non-root admin account before revocation.' >&2
    exit 1
}
for path in sys/mounts sys/auth sys/policies/acl/admin sys/leases/lookup identity/entity; do
    vault token capabilities -format=json "$path" |
        jq -e 'index("create") != null and index("read") != null and index("update") != null
            and index("delete") != null and index("list") != null and index("sudo") != null' >/dev/null
done
vault secrets list -format=json >/dev/null
vault auth list -format=json >/dev/null

root_token=$(cat "$ROOT_TOKEN_FILE")
[ -n "$root_token" ]
VAULT_TOKEN="$root_token" vault token lookup -format=json |
    jq -e '.data.policies == ["root"] and .data.path == "auth/token/root"' >/dev/null

# Normal revocation also revokes descendants and leases: review them first.
VAULT_TOKEN="$root_token" vault token revoke -self >/dev/null
vault token lookup -format=json >/dev/null
vault secrets list -format=json >/dev/null
rm -- "$ROOT_TOKEN_FILE"
unset root_token
echo 'Bootstrap root token revoked; independent administrator access verified.'
