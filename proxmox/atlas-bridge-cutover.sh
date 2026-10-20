#!/usr/bin/env bash
# Copy Bridge's final stopped state; retain the old source and Atlas staging.
set -euo pipefail
cd "$(dirname "$0")/.."
source_host=user_l@docker.home.arpa
target_host=user_l@atlas.home.arpa
image=sha256:81c4c5db65afa3a490c35a49ca9ed8e03f457fd76dcdd8e047e35a3d89361c06
ssh -o BatchMode=yes "$target_host" sudo -n docker image inspect "$image" --format '{{.Id}}'
ssh -o BatchMode=yes "$target_host" 'sudo -n tee /root/openvox-infra/stacks/atlas-background-prepare.py >/dev/null' < proxmox/atlas-background-prepare.py
ssh -o BatchMode=yes "$target_host" sudo -n python3 - "$image" <<'PY'
import json
from pathlib import Path
import sys
path = Path('/root/openvox-infra/stacks/source-state.json')
state = json.loads(path.read_text())
state['protonmail-bridge']['images']['protonmail-bridge'] = sys.argv[1]
path.write_text(json.dumps(state, indent=2) + '\n')
path.chmod(0o600)
PY
ssh -o BatchMode=yes "$target_host" sudo -n python3 /root/openvox-infra/stacks/atlas-background-prepare.py
ssh -o BatchMode=yes "$source_host" sudo -n bash -s -- protonmail-bridge < proxmox/docker-retire-background.sh
ssh -o BatchMode=yes "$target_host" 'sudo -n install -d -m 0700 /root/openvox-infra/background-cutover/protonmail-bridge/final-volumes'
ssh -o BatchMode=yes "$source_host" 'sudo -n tar --acls --xattrs --numeric-owner -C /var/lib/docker/100000.100000/volumes -cf - protonmail-bridge_bridge-state/_data' |
  ssh -o BatchMode=yes "$target_host" 'sudo -n tar --acls --xattrs --numeric-owner -C /root/openvox-infra/background-cutover/protonmail-bridge/final-volumes -xf -'
ssh -o BatchMode=yes "$source_host" 'sudo -n tar --acls --xattrs --numeric-owner -C /opt/docker/protonmail-bridge -cf - secrets' |
  ssh -o BatchMode=yes "$target_host" 'sudo -n tar --acls --xattrs --numeric-owner -C /opt/docker/protonmail-bridge -xf -'
ssh -o BatchMode=yes "$target_host" sudo -n python3 - protonmail-bridge < proxmox/atlas-background-final-restore.py
ssh -o BatchMode=yes "$target_host" 'sudo -n docker compose --env-file /dev/null -f /opt/docker/protonmail-bridge/atlas-compose.json up -d --pull never --no-build --wait --wait-timeout 180'
