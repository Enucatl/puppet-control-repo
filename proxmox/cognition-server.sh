#!/bin/bash
set -euo pipefail
# Retained source provisioning reference; production uses cerebrum-server.sh.
# Do not rebuild VM 220 while it is retained for rollback.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Preserve Complex's container ownership when copying its persistent state.
export DOCKER_USERNS_REMAP=""
exec bash "$SCRIPT_DIR/docker-server.sh" -n cognition -i 220 -t 9001 \
    -s openvox.home.arpa -m 32768 -d 1000G -a bc:24:11:4e:c0:22 -v virtio "$@"
