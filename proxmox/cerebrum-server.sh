#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Preserve Cognition's numeric container ownership through the migration.
export DOCKER_USERNS_REMAP=""
exec bash "$SCRIPT_DIR/docker-server.sh" -n cerebrum -i 230 -t 9002 \
    -s openvox.home.arpa -m 32768 -d 1000G -a bc:24:11:4e:c0:23 -v virtio "$@"
