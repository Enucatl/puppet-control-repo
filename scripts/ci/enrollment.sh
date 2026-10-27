#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../.."
VAULT_VERSION=2.1.0
RUNNER_TEMP=$(mktemp -d)
trap 'rm -rf "$RUNNER_TEMP"' EXIT

sudo apt-get update
sudo apt-get install -y gettext-base jq
pushd "$RUNNER_TEMP"
curl -fsSLO "https://releases.hashicorp.com/vault/${VAULT_VERSION}/vault_${VAULT_VERSION}_linux_amd64.zip"
curl -fsSLO "https://releases.hashicorp.com/vault/${VAULT_VERSION}/vault_${VAULT_VERSION}_SHA256SUMS"
grep " vault_${VAULT_VERSION}_linux_amd64.zip$" "vault_${VAULT_VERSION}_SHA256SUMS" | sha256sum -c -
unzip "vault_${VAULT_VERSION}_linux_amd64.zip" vault
sudo install vault /usr/local/bin/vault
popd

for script in .githooks/pre-push scripts/check.sh scripts/ci/*.sh tests/test_pre_push.sh proxmox/*.sh docker/openvox/*.sh docker/openvox/infra-{host-post-receive,post-receive} docker/vault/scripts/*.sh; do bash -n "$script"; done
uv run --frozen ruff check proxmox/atlas-*.py scripts/autosign.py scripts/openvox*.py docker/openvox/vault-api.py docker/openvox/infra-pki-self-test.py proxmox/desktop.py proxmox/desktop_cloud_init.py tests/test_autosign*.py tests/test_openvox*.py tests/test_offline_vault*.py tests/test_puppet_enrollment_vault.py tests/test_puppet_pki_vault.py tests/test_vault_unseal_pin.py tests/test_puppet_provisioning.py tests/test_desktop_provisioning.py tests/test_atlas_*.py tests/test_router_dns.py
uv run --frozen ruff format --check proxmox/atlas-*.py scripts/autosign.py scripts/openvox*.py docker/openvox/vault-api.py docker/openvox/infra-pki-self-test.py proxmox/desktop.py proxmox/desktop_cloud_init.py tests/test_autosign*.py tests/test_openvox*.py tests/test_offline_vault*.py tests/test_puppet_enrollment_vault.py tests/test_puppet_pki_vault.py tests/test_vault_unseal_pin.py tests/test_puppet_provisioning.py tests/test_desktop_provisioning.py tests/test_atlas_*.py tests/test_router_dns.py
uv run --frozen python scripts/openvox_rehearsal_recovery.py
uv run --frozen python -m pytest -q tests/test_puppet_enrollment_vault.py tests/test_puppet_pki_vault.py

bash tests/test_pre_push.sh
