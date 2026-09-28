#!/usr/bin/env bash
# Isolated regression checks: mocked failures and a disposable local Vault server.
set -euo pipefail

repo=$(cd "$(dirname "$0")/.." && pwd)
scripts=$repo/docker/vault/scripts
work=$(mktemp -d)
server_pid=
cleanup() {
    if [ -n "$server_pid" ]; then
        kill "$server_pid" 2>/dev/null || true
        wait "$server_pid" 2>/dev/null || true
    fi
    rm -rf "$work"
}
trap cleanup EXIT
original_path=$PATH
export TEST_WORK=$work
export VAULT_ADDR=http://vault.invalid:8200
export VAULT_TOKEN=test-independent-admin
export INIT_FILE=$work/recovery/init.json
export ROOT_TOKEN_FILE=$work/bootstrap/root-token
export UNSEAL_KEY_FILE=$work/unseal/key
mkdir "$work/bin"
export PATH=$work/bin:$PATH

cat > "$work/bin/curl" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$TEST_WORK/argv"
case "${*: -1}" in
    */sys/health)
        case ${TEST_CASE:-init} in
            unreachable) exit 7 ;;
            bad-health) echo '{}' ;;
            init|init-fail|uninitialized) echo '{"initialized":false,"sealed":true}' ;;
            *) echo '{"initialized":true,"sealed":true}' ;;
        esac ;;
    */sys/init)
        [[ "${TEST_CASE:-init}" == init* ]]
        output=
        while [ "$#" -gt 0 ]; do
            if [ "$1" = --output ]; then output=$2; shift; fi
            shift
        done
        [ -n "$output" ]
        [ "$(stat -c %a "$output")" = 600 ]
        printf '%s\n' '{"keys_base64":["test-secret-unseal-share"],"root_token":"test-secret-bootstrap-token"}' > "$output"
        if [ "${TEST_CASE:-init}" = init-fail ]; then exit 22; fi ;;
    */sys/unseal)
        [[ " $* " == *' --data-binary @- '* ]]
        jq -e '.key == "test-secret-unseal-share"' >/dev/null
        touch "$TEST_WORK/unseal-request"
        if [ "${TEST_CASE:-}" = unseal-fail ]; then
            echo '{"errors":["test-secret-unseal-share"]}'
            exit 22
        fi
        echo '{"sealed":false}' ;;
    *) exit 99 ;;
esac
MOCK
cat > "$work/bin/sleep" <<'MOCK'
#!/usr/bin/env bash
exit 37
MOCK
cat > "$work/bin/vault" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$TEST_WORK/argv"
case "$1 $2" in
    'token lookup')
        if [ "${VAULT_TOKEN:-}" = test-secret-bootstrap-token ]; then
            if [ "${TEST_CASE:-}" = wrong-bootstrap ]; then
                echo '{"data":{"policies":["admin"],"path":"auth/ldap/login/admin"}}'
            else
                echo '{"data":{"policies":["root"],"path":"auth/token/root"}}'
            fi
        else
            case ${TEST_CASE:-} in
                root-admin) echo '{"data":{"policies":["root"],"orphan":true}}' ;;
                child-admin) echo '{"data":{"policies":["admin"],"orphan":false}}' ;;
                *) echo '{"data":{"policies":["default"],"identity_policies":["admin"],"orphan":true}}' ;;
            esac
        fi ;;
    'token capabilities')
        if [ "${TEST_CASE:-}" = weak-admin ]; then echo '["read"]';
        else echo '["create","read","update","delete","list","sudo"]'; fi ;;
    'secrets list'|'auth list') echo '{}' ;;
    'token revoke')
        [ "${VAULT_TOKEN:-}" = test-secret-bootstrap-token ]
        [ "$3" = -self ]
        if [ "${TEST_CASE:-}" = revoke-fail ]; then exit 1; fi
        touch "$TEST_WORK/revoked" ;;
    *) exit 99 ;;
esac
MOCK
chmod +x "$work/bin/"*

assert_private() {
    ! rg -q 'test-secret-(unseal-share|bootstrap-token)' "$work/output" "$work/argv"
}
run_unsealer() {
    local result=0
    bash -x "$scripts/unseal.sh" > "$work/output" 2>&1 || result=$?
    [ "$result" = 37 ] # fake sleep terminates after one complete iteration
    assert_private
}

bash -x "$scripts/00-vault-init.sh" > "$work/output" 2>&1
assert_private
[ "$(cat "$ROOT_TOKEN_FILE")" = test-secret-bootstrap-token ]
[ "$(cat "$UNSEAL_KEY_FILE")" = test-secret-unseal-share ]
for file in "$INIT_FILE" "$ROOT_TOKEN_FILE" "$UNSEAL_KEY_FILE"; do
    [ "$(stat -c %a "$file")" = 600 ]
    [ "$(stat -c %a "$(dirname "$file")")" = 700 ]
done
before=$(sha256sum "$INIT_FILE" "$ROOT_TOKEN_FILE" "$UNSEAL_KEY_FILE")
if bash "$scripts/00-vault-init.sh" > "$work/output" 2>&1; then exit 1; fi
[ "$(sha256sum "$INIT_FILE" "$ROOT_TOKEN_FILE" "$UNSEAL_KEY_FILE")" = "$before" ]
export TEST_CASE=sealed
if bash "$scripts/00-vault-init.sh" > "$work/output" 2>&1; then exit 1; fi
[ "$(sha256sum "$INIT_FILE" "$ROOT_TOKEN_FILE" "$UNSEAL_KEY_FILE")" = "$before" ]
assert_private
printf '%s\n' 'PASS: initialization saves restricted files, does not log credentials, refuses overwrite'

(
    export INIT_FILE=$work/interrupted/recovery/init.json
    export ROOT_TOKEN_FILE=$work/interrupted/bootstrap/root-token
    export UNSEAL_KEY_FILE=$work/interrupted/unseal/key
    for TEST_CASE in bad-health unreachable; do
        export TEST_CASE
        if bash "$scripts/00-vault-init.sh" > "$work/output" 2>&1; then exit 1; fi
        [ ! -e "$INIT_FILE" ]
    done
    export TEST_CASE=init-fail
    if bash "$scripts/00-vault-init.sh" > "$work/output" 2>&1; then exit 1; fi
    [ -s "$INIT_FILE" ]
    [ ! -e "$ROOT_TOKEN_FILE" ]
    [ ! -e "$UNSEAL_KEY_FILE" ]
    assert_private
    export TEST_CASE=sealed
    bash "$scripts/00-vault-init.sh" > "$work/output" 2>&1
    [ "$(cat "$ROOT_TOKEN_FILE")" = test-secret-bootstrap-token ]
    [ "$(cat "$UNSEAL_KEY_FILE")" = test-secret-unseal-share ]
    assert_private
)
printf '%s\n' 'PASS: init rejects unknown health, preserves failed response, and resumes splitting saved credentials'

: > "$work/argv"
for TEST_CASE in sealed unseal-fail; do
    export TEST_CASE
    rm -f "$work/unseal-request"
    run_unsealer
    [ -e "$work/unseal-request" ]
done
for TEST_CASE in uninitialized unreachable; do
    export TEST_CASE
    rm -f "$work/unseal-request"
    run_unsealer
    [ ! -e "$work/unseal-request" ]
done
export TEST_CASE=sealed
mv "$UNSEAL_KEY_FILE" "$work/saved-key"
rm -f "$work/unseal-request"
run_unsealer
[ ! -e "$work/unseal-request" ]
mv "$work/saved-key" "$UNSEAL_KEY_FILE"
! rg -q '/sys/init' "$work/argv"
printf '%s\n' 'PASS: unsealer uses stdin, handles failures/missing keys, never initializes or logs secrets'

for TEST_CASE in root-admin child-admin weak-admin wrong-bootstrap revoke-fail; do
    export TEST_CASE
    if bash -x "$scripts/99-revoke-root-token.sh" > "$work/output" 2>&1; then exit 1; fi
    [ -s "$ROOT_TOKEN_FILE" ]
    [ ! -e "$work/revoked" ]
    assert_private
done
export TEST_CASE=admin
bash -x "$scripts/99-revoke-root-token.sh" > "$work/output" 2>&1
[ -e "$work/revoked" ]
[ ! -e "$ROOT_TOKEN_FILE" ]
assert_private
printf '%s\n' 'PASS: revocation requires independent capable admin and root bootstrap, retains file on failure'

# Compose expands its existing baseline; config is never printed (it can contain environment secrets).
docker compose -f "$repo/docker/docker-compose.yml" --profile init --profile setup config --format json > "$work/compose.json"
jq -e '
    .services as $s |
    ([ $s.vault.volumes[].source ] | index("vault_recovery") == null and index("vault_bootstrap") == null and index("vault_unseal") == null) and
    ([ $s["vault-unsealer"].volumes[] | select(.type == "volume") ] | length == 1 and .[0].source == "vault_unseal" and .[0].read_only == true) and
    ([ $s["vault-init"].volumes[] | select(.type == "volume") | .source ] | sort == ["vault_bootstrap","vault_recovery","vault_unseal"]) and
    ([$s | to_entries[] | select(.key != "vault-init") | .value.volumes[]? | select(.source == "vault_recovery")] | length == 0) and
    ([$s | to_entries[] | select(.key != "vault-init" and .key != "vault-unsealer") | .value.volumes[]? | select(.source == "vault_unseal")] | length == 0)
' "$work/compose.json" >/dev/null
printf '%s\n' 'PASS: Compose isolates recovery/bootstrap/unseal volumes and makes the unsealer share read-only'

# Run the same scripts against an uninitialized, disposable real Vault server.
export PATH=$original_path
unset VAULT_TOKEN VAULT_CACERT VAULT_CAPATH VAULT_NAMESPACE VAULT_AGENT_ADDR
export VAULT_ADDR=http://127.0.0.1:$(shuf -i 25000-45000 -n 1)
mkdir "$work/data" "$work/real"
export INIT_FILE=$work/real/recovery/init.json
export ROOT_TOKEN_FILE=$work/real/bootstrap/root-token
export UNSEAL_KEY_FILE=$work/real/unseal/key
cat > "$work/server.hcl" <<CONFIG
disable_mlock = true
storage "file" { path = "$work/data" }
listener "tcp" {
  address = "${VAULT_ADDR#http://}"
  tls_disable = true
}
api_addr = "$VAULT_ADDR"
CONFIG
vault server -config="$work/server.hcl" > "$work/server.log" 2>&1 &
server_pid=$!
for ((attempt=0;attempt<100;attempt++)); do
    kill -0 "$server_pid"
    if curl --silent --max-time 1 "$VAULT_ADDR/v1/sys/health" | jq -e '.initialized == false' >/dev/null; then break; fi
    sleep 0.1
done
bash "$scripts/00-vault-init.sh" > "$work/output" 2>&1
real_root=$(cat "$ROOT_TOKEN_FILE")
real_key=$(cat "$UNSEAL_KEY_FILE")
check_real_logs() {
    ! rg -q -F -e "$real_root" -e "$real_key" "$work/output" "$work/server.log"
}
check_real_logs
for file in "$INIT_FILE" "$ROOT_TOKEN_FILE" "$UNSEAL_KEY_FILE"; do
    [ "$(stat -c %a "$file")" = 600 ]
done
if bash "$scripts/00-vault-init.sh" >> "$work/output" 2>&1; then exit 1; fi
# Only sleep is mocked here: both HTTP requests and unsealing are real.
mkdir "$work/once"
cp "$work/bin/sleep" "$work/once/sleep"
result=0
PATH="$work/once:$PATH" bash "$scripts/unseal.sh" >> "$work/output" 2>&1 || result=$?
[ "$result" = 37 ]
vault status -format=json | jq -e '.initialized and (.sealed == false)' >/dev/null
check_real_logs
export VAULT_TOKEN=$real_root
sed -n "/^vault policy write admin - <<'EOF'$/,/^EOF$/p" "$scripts/13-vault-admin-policy.sh" | sed '1d;$d' > "$work/admin.hcl"
vault policy write admin "$work/admin.hcl" >/dev/null
admin_token=$(vault token create -orphan -policy=admin -ttl=5m -format=json | jq -r .auth.client_token)
export VAULT_TOKEN=$admin_token
vault operator seal >/dev/null
result=0
PATH="$work/once:$PATH" bash "$scripts/unseal.sh" >> "$work/output" 2>&1 || result=$?
[ "$result" = 37 ]
vault status -format=json | jq -e '.sealed == false' >/dev/null
bash "$scripts/99-revoke-root-token.sh" >> "$work/output" 2>&1
[ ! -e "$ROOT_TOKEN_FILE" ]
if VAULT_TOKEN=$real_root vault token lookup >/dev/null 2>&1; then exit 1; fi
vault secrets enable -path=after-revoke kv >/dev/null 2>&1
vault kv put after-revoke/check value=works >/dev/null
[ "$(vault kv get -field=value after-revoke/check)" = works ]
vault secrets disable after-revoke >/dev/null 2>&1
check_real_logs
printf '%s\n' 'PASS: real Vault init, re-init refusal, seal/unseal, root revocation, and admin writes afterward'
