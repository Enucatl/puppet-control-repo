#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
compose=(docker compose -f "$script_dir/../docker-compose.yml")

"${compose[@]}" build vault-unsealer
"${compose[@]}" up -d vault
"${compose[@]}" run --rm vault-init
"${compose[@]}" up -d vault-unsealer
