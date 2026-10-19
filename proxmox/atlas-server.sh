#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$SCRIPT_DIR/docker-server.sh" -b -n atlas -i 210 -m 8192 -d 300G -a bc:24:11:4e:a4:9e "$@"
