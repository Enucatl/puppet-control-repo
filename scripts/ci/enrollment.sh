#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../.."
if [[ ${PUPPET_CHECK_PREPARED:-} != 1 ]]; then scripts/ci/install-enrollment.sh; fi

for script in .githooks/pre-push scripts/check.sh scripts/ci/*.sh tests/test_pre_push.sh proxmox/*.sh docker/openvox/*.sh docker/openvox/infra-{host-post-receive,post-receive} docker/vault/scripts/*.sh; do bash -n "$script"; done
uv run --frozen ruff check proxmox/atlas-*.py scripts/autosign.py scripts/openvox*.py docker/openvox/vault-api.py docker/openvox/infra-pki-self-test.py proxmox/desktop.py proxmox/desktop_cloud_init.py tests/test_autosign*.py tests/test_openvox*.py tests/test_offline_vault*.py tests/test_puppet_enrollment_vault.py tests/test_puppet_pki_vault.py tests/test_vault_unseal_pin.py tests/test_puppet_provisioning.py tests/test_desktop_provisioning.py tests/test_atlas_*.py tests/test_router_dns.py
uv run --frozen ruff format --check proxmox/atlas-*.py scripts/autosign.py scripts/openvox*.py docker/openvox/vault-api.py docker/openvox/infra-pki-self-test.py proxmox/desktop.py proxmox/desktop_cloud_init.py tests/test_autosign*.py tests/test_openvox*.py tests/test_offline_vault*.py tests/test_puppet_enrollment_vault.py tests/test_puppet_pki_vault.py tests/test_vault_unseal_pin.py tests/test_puppet_provisioning.py tests/test_desktop_provisioning.py tests/test_atlas_*.py tests/test_router_dns.py
uv run --frozen python scripts/openvox_rehearsal_recovery.py
uv run --frozen python -m pytest -q tests/test_puppet_enrollment_vault.py tests/test_puppet_pki_vault.py

bash tests/test_pre_push.sh
