#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
compose=(docker compose -f "$script_dir/../docker-compose.yml" -f "$script_dir/bootstrap.compose.yml")

"${compose[@]}" build vault-unsealer vault-pki-core-setup
trap '"${compose[@]}" stop vault-unsealer vault' EXIT
"${compose[@]}" up -d vault
"${compose[@]}" run --rm vault-init
"${compose[@]}" up -d --wait --wait-timeout 180 vault-unsealer
"${compose[@]}" run --rm vault-pki-core-setup
"${compose[@]}" stop vault-unsealer vault
trap - EXIT

echo 'Vault initialized and TLS certificates generated; the HTTP bootstrap server is stopped.'
echo 'Install the generated CA in the host trust store, then start Vault using the normal Compose file (see README.md).'
