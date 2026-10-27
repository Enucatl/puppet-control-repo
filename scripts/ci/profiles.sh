#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../.."
export PUPPET_TEST_MODULEPATH=/tmp/openvox-ci-modules
OPENVOX_RELEASE_URL=https://github.com/Enucatl/openvox-packages/releases/download/resolute-28001
OPENVOX_AGENT_VERSION=9.1.1+homelab.28001-1+ubuntu24.04
RUNNER_TEMP=$(mktemp -d)
trap 'rm -rf "$RUNNER_TEMP"' EXIT

test "$(. /etc/os-release; printf '%s' "$VERSION_ID")" = 24.04
test "$(dpkg --print-architecture)" = amd64
curl -fsSLo "$RUNNER_TEMP/openvox-archive-keyring.asc" https://raw.githubusercontent.com/Enucatl/openvox-packages/4f719cb678cc06a6f8c57a2e4a6a6915b84413c9/openvox-archive-keyring.asc
test "$(gpg --show-keys --with-colons "$RUNNER_TEMP/openvox-archive-keyring.asc" | awk -F: '$1 == "pub" { primary = 1; next } primary && $1 == "fpr" { print $10; primary = 0 }')" = 775661C709ACE6F7C1CF0FF6C4B7528F03E0BB94
sudo install -d -m 0755 /etc/apt/keyrings
sudo install -m 0644 "$RUNNER_TEMP/openvox-archive-keyring.asc" /etc/apt/keyrings/openvox-archive-keyring.asc
curl -fsSLo "$RUNNER_TEMP/openvox.pref" "$OPENVOX_RELEASE_URL/ubuntu24.04.pref"
sudo install -m 0644 "$RUNNER_TEMP/openvox.pref" /etc/apt/preferences.d/openvox-homelab
printf 'deb [arch=amd64 signed-by=/etc/apt/keyrings/openvox-archive-keyring.asc] %s/ ./\n' "$OPENVOX_RELEASE_URL" | sudo tee /etc/apt/sources.list.d/openvox-homelab.list >/dev/null
sudo apt-get update
sudo apt-get install -y "openvox-agent=$OPENVOX_AGENT_VERSION"
test "$(dpkg-query -W -f='${Version}' openvox-agent)" = "$OPENVOX_AGENT_VERSION"
if [[ -d /run/systemd/system ]]; then sudo systemctl stop puppet; fi
/opt/puppetlabs/puppet/bin/ruby -ropenssl -e 'key = OpenSSL::PKey.generate_key("ML-DSA-44"); abort unless key.verify(nil, key.sign(nil, "ci"), "ci")'

sudo /opt/puppetlabs/puppet/bin/gem install --no-document r10k --version 5.0.3
/opt/puppetlabs/puppet/bin/r10k puppetfile install --puppetfile Puppetfile --moduledir "$PUPPET_TEST_MODULEPATH"
test -d "$PUPPET_TEST_MODULEPATH/stdlib"
test -d "$PUPPET_TEST_MODULEPATH/systemd"

set -o pipefail
/opt/puppetlabs/bin/puppet parser validate modules/profile/manifests modules/proxmox_workflows/manifests
/opt/puppetlabs/bin/puppet parser validate docker/openvox/fixture/manifests
find modules/profile/templates -name '*.epp' -print0 | xargs -0 -r /opt/puppetlabs/bin/puppet epp validate
for script in docker/openvox/*.rb docker/vault/scripts/offline-intermediate.rb; do /opt/puppetlabs/puppet/bin/ruby -c "$script"; done

sudo apt-get install -y build-essential libssh-dev
uv sync --frozen --project provisioning
export PATH="$PWD/provisioning/.venv/bin:$PATH"

uv run --frozen python -m pytest -q tests --ignore=tests/test_puppet_enrollment_vault.py --ignore=tests/test_puppet_pki_vault.py
