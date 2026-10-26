#!/usr/bin/env bash
# Exercise the real server without exposing DNS/DHCP ports on the host.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
name="technitium-test-$$"
cleanup() {
    docker rm --force "$name" >/dev/null 2>&1 || true
    docker volume rm "$name" >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker volume create "$name" >/dev/null

docker run --rm --userns=host --detach --name "$name" \
    --env DNS_SERVER_ADMIN_PASSWORD=test-password \
    --env DNS_SERVER_WEB_SERVICE_LOCAL_ADDRESSES=127.0.0.1,[::1] \
    technitium/dns-server:15.6.2 >/dev/null

run_tests() {
    docker run --rm --userns=host --cap-add NET_ADMIN \
        --network "container:$name" --volume "$root:/repo:ro" \
        --volume "$name:/fixtures" --env TECHNITIUM_TEST_CONFIG=/fixtures \
        --env TECHNITIUM_API_URL=http://127.0.0.1:5380/api/ \
        --env TECHNITIUM_SOURCE_TEST="$1" \
        --env TECHNITIUM_VERIFY_PERSISTED="$2" \
        ghcr.io/astral-sh/uv:python3.13-bookworm bash -euc '
            apt-get update -qq
            apt-get install -y -qq --no-install-recommends jq curl iproute2 libssh-dev busybox >/dev/null
            cp /repo/provisioning/pyproject.toml /repo/provisioning/uv.lock /tmp/
            cp -r /repo/provisioning/src /tmp/
            cd /tmp
            uv sync --frozen --quiet
            for attempt in {1..30}; do
                if curl --silent --fail http://127.0.0.1:5380/api/user/login --data "user=admin&pass=test-password" | jq -e '\''.status == "ok"'\'' >/dev/null; then
                    break
                fi
                sleep 1
            done
            export PYTHONDONTWRITEBYTECODE=1
            uv run --with pytest --quiet python -m pytest -p no:cacheprovider "$@"
        ' bash "${@:3}"
}

tests=("$root"/tests/test_technitium*.py "$root"/tests/test_router_dns.py)
run_tests 1 0 "${tests[@]/$root/\/repo}"
docker restart --time 30 "$name" >/dev/null
run_tests 0 1 /repo/tests/test_technitium.py::test_configuration_and_persistence

docker run --rm --userns=host --network none \
    --volume "$name:/fixtures:ro" grafana/alloy:v1.21.0 \
    validate --stability.level=experimental /fixtures/alloy/config.alloy
