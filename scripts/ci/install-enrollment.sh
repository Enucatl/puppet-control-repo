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

