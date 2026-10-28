#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../.."
export PUPPET_TEST_MODULEPATH=/tmp/openvox-ci-modules
if [[ ${PUPPET_CHECK_PREPARED:-} != 1 ]]; then scripts/ci/install-profiles.sh; fi
/opt/puppetlabs/puppet/bin/ruby -ropenssl -e 'key = OpenSSL::PKey.generate_key("ML-DSA-44"); abort unless key.verify(nil, key.sign(nil, "ci"), "ci")'

/opt/puppetlabs/bin/puppet parser validate modules/profile/manifests modules/proxmox_workflows/manifests
/opt/puppetlabs/bin/puppet parser validate docker/openvox/fixture/manifests
find modules/profile/templates -name '*.epp' -print0 | xargs -0 -r /opt/puppetlabs/bin/puppet epp validate
for script in docker/openvox/*.rb docker/vault/scripts/offline-intermediate.rb; do /opt/puppetlabs/puppet/bin/ruby -c "$script"; done

export PATH="$PWD/provisioning/.venv/bin:$PATH"

uv run --frozen python -m pytest -q tests --ignore=tests/test_puppet_enrollment_vault.py --ignore=tests/test_puppet_pki_vault.py
