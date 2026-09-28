#!/usr/bin/env bash
# Mocked orchestration/config checks; --integration also boots disposable Vault containers.
set -euo pipefail
umask 077

repo=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
real_docker=$(command -v docker)
project="vault-bootstrap-test-${UID}-$$"
cleanup() {
    if [[ -f $work/isolated.json ]]; then
        "$real_docker" compose -p "$project" -f "$work/isolated.json" --profile init --profile setup down --volumes --remove-orphans >/dev/null 2>&1 || true
        "$real_docker" image rm "$project-setup" "$project-unsealer" >/dev/null 2>&1 || true
    fi
    rm -rf -- "$work"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'echo "FAIL: Vault bootstrap regression check at line $LINENO" >&2' ERR
export TEST_WORK=$work TEST_REPO=$repo
mkdir "$work/bin"
cat > "$work/bin/docker" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
[[ $1 == compose && $2 == -f && $3 == "$TEST_REPO/docker/vault/../docker-compose.yml" ]]
[[ $4 == -f && $5 == "$TEST_REPO/docker/vault/bootstrap.compose.yml" ]]
shift 5
printf '%s\n' "$*" >> "$TEST_WORK/commands"
if [[ ${TEST_FAIL_PKI:-0} == 1 && $* == 'run --rm vault-pki-core-setup' ]]; then exit 42; fi
MOCK
chmod +x "$work/bin/docker"
PATH="$work/bin:$PATH" bash "$repo/docker/vault/bootstrap.sh" >"$work/output" 2>&1
cat > "$work/expected" <<'EXPECTED'
build vault-unsealer vault-pki-core-setup
up -d vault
run --rm vault-init
up -d --wait --wait-timeout 180 vault-unsealer
run --rm vault-pki-core-setup
stop vault-unsealer vault
EXPECTED
cmp "$work/expected" "$work/commands"
echo 'PASS: bootstrap builds setup tooling, initializes before waiting for unseal, generates TLS, and stops HTTP'

: >"$work/commands"
status=0
TEST_FAIL_PKI=1 PATH="$work/bin:$PATH" bash "$repo/docker/vault/bootstrap.sh" >"$work/output" 2>&1 || status=$?
[[ $status == 42 ]]
cmp "$work/expected" "$work/commands"
echo 'PASS: failed certificate generation preserves failure status and stops the HTTP bootstrap server'

compose=("$real_docker" compose -f "$repo/docker/docker-compose.yml" -f "$repo/docker/vault/bootstrap.compose.yml" --profile init --profile setup)
"${compose[@]}" config --format json >"$work/config.json" 2>"$work/config.err"
jq -e '
  .services as $s |
  $s.vault.command == ["vault", "server", "-config", "/vault/config/vault-conf-insecure.hcl"] and
  ($s.vault.ports | length == 1) and $s.vault.ports[0].host_ip == "127.0.0.1" and
  $s.vault.environment.VAULT_ADDR == "http://127.0.0.1:8200" and
  $s.vault.environment.VAULT_CACERT == "" and
  (["vault-init", "vault-unsealer", "vault-pki-core-setup"] | all(.[]; $s[.].environment.VAULT_ADDR == "http://vault:8200")) and
  $s["vault-init"].depends_on.vault.condition == "service_healthy" and
  $s["vault-unsealer"].depends_on.vault.condition == "service_started" and
  $s["vault-pki-core-setup"].depends_on["vault-unsealer"].condition == "service_healthy" and
  $s["vault-pki-core-setup"].build.context != null and
  $s["vault-pki-core-setup"].user == "100:100"
' "$work/config.json" >/dev/null
echo 'PASS: merged bootstrap configuration selects HTTP, localhost publishing, and noncircular readiness dependencies'
"$real_docker" compose -f "$repo/docker/docker-compose.yml" --profile init --profile setup config --format json >"$work/normal.json" 2>"$work/config.err"
jq -e '
  .services as $s |
  $s.vault.command == ["vault", "server", "-config", "/vault/config/vault-conf.hcl"] and
  (["vault", "vault-pki-intermediate-setup", "freeipa-sign-csr"] | all(.[]; $s[.].environment.VAULT_CACERT == "/certificates/ca.crt"))
' "$work/normal.json" >/dev/null
echo 'PASS: normal server and TLS setup jobs use the generated, mounted CA certificate'

if [[ ${1:-} != --integration ]]; then
    echo 'SKIP: disposable Compose smoke test (pass --integration to run)'
    exit 0
fi

# Retain the actual service definitions, hardening, bind mounts, and dependencies.
# Replace all Docker resource names and publish no ports. Never join production networks.
jq --arg project "$project" '
  .services |= with_entries(select(.key | IN("vault", "vault-init", "vault-unsealer", "vault-pki-core-setup"))) |
  .services |= with_entries(.value |= (
    del(.ports, .container_name) |
    .restart = "no" |
    .networks = {infra: {}} |
    .volumes |= map(if .type == "bind" then .read_only = true else . end)
  )) |
  .name = $project |
  .services.vault.networks.infra.aliases = ["vault.home.arpa"] |
  .services["vault-pki-core-setup"].image = ($project + "-setup") |
  .services["vault-unsealer"].image = ($project + "-unsealer") |
  .services["vault-init"].image = ($project + "-unsealer") |
  .networks = {infra: {name: ($project + "-network")}} |
  .volumes |= with_entries(select(.key | IN("certificates", "vault_data", "vault_logs", "vault_recovery", "vault_bootstrap", "vault_unseal")) | .value = {name: ($project + "-" + .key)}) |
  del(.secrets)
' "$work/config.json" >"$work/isolated.json"
jq -e --arg prefix "$project-" '
  ([.volumes[].name, .networks[].name] | all(.[]; startswith($prefix))) and
  ([.services[].ports[]?] | length == 0) and
  ([.services[].volumes[] | select(.type == "bind")] | all(.[]; .target == "/vault/config" or .target == "/scripts" or (.target | startswith("/scripts/")) or .target == "/etc/ssl/certs/ca-certificates.crt"))
' "$work/isolated.json" >/dev/null

# Route bootstrap.sh's exact commands to the isolated configuration.
export TEST_DOCKER=$real_docker TEST_PROJECT=$project
cat > "$work/bin/docker" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
[[ $1 == compose && $2 == -f && $4 == -f ]]
shift 5
exec "$TEST_DOCKER" compose -p "$TEST_PROJECT" -f "$TEST_WORK/isolated.json" "$@"
WRAPPER
PATH="$work/bin:$PATH" timeout 360 bash "$repo/docker/vault/bootstrap.sh" >"$work/bootstrap.log" 2>&1
isolated=("$real_docker" compose -p "$project" -f "$work/isolated.json")
[[ -z $("${isolated[@]}" ps --status running -q) ]]
"${isolated[@]}" run --rm --no-deps --entrypoint /bin/sh vault-pki-core-setup -ec '
  test -s /certificates/ca.crt
  test -s /certificates/vault.crt
  test -s /certificates/vault.key
  test -s /certificates/vault_chain.crt
' >"$work/cert-check.log" 2>&1

# Restart the same fresh data/certificates with the normal TLS server config.
jq --slurpfile normal "$work/normal.json" '
  .services.vault.command = $normal[0].services.vault.command |
  .services.vault.environment.VAULT_ADDR = "https://127.0.0.1:8200" |
  .services.vault.environment.VAULT_CACERT = $normal[0].services.vault.environment.VAULT_CACERT |
  .services["vault-unsealer"].environment.VAULT_ADDR = "https://vault.home.arpa:8200" |
  .services["vault-unsealer"].environment.CURL_CA_BUNDLE = "/certificates/ca.crt" |
  .services["vault-unsealer"].volumes += [{type: "volume", source: "certificates", target: "/certificates", read_only: true}]
' "$work/isolated.json" >"$work/tls.json"
tls=("$real_docker" compose -p "$project" -f "$work/tls.json")
"${tls[@]}" up -d --wait --wait-timeout 180 vault-unsealer >"$work/tls.log" 2>&1
"${tls[@]}" exec -T vault vault status -format=json | jq -e '.initialized and (.sealed == false)' >/dev/null
"${tls[@]}" logs --no-color vault vault-unsealer >"$work/runtime.log"
# Inspect secrets only inside their container; print no credentials or logs.
"${isolated[@]}" run --rm --no-deps --entrypoint /bin/sh vault-init -ec '
  jq -r ".root_token, .keys_base64[]" /recovery/init.json
' >"$work/secret-patterns" 2>"$work/read-recovery.log"
! grep -Fq -f "$work/secret-patterns" "$work/bootstrap.log" "$work/runtime.log" "$work/tls.log"
echo 'PASS: disposable fresh Vault initializes, unseals, generates TLS, stops HTTP, and restarts with verified HTTPS'
