#!/usr/bin/env bash
# Release legacy server RAM before allocating Atlas and the replacement desktop.
set -euo pipefail
ssh -o BatchMode=yes user_l@docker.home.arpa 'sudo -n systemctl mask --now puppetserver.service && ! sudo -n systemctl is-active --quiet puppetserver.service'
ssh -o BatchMode=yes root@proxmox.home.arpa bash -s <<'HOST'
set -euo pipefail
[[ $(qm config 200 | sed -n 's/^name: //p') == docker ]]
[[ $(qm config 210 | sed -n 's/^name: //p') == atlas ]]
qm shutdown 200 --timeout 180
[[ $(qm status 200) == 'status: stopped' ]]
qm set 200 --memory 4096
qm start 200
qm shutdown 210 --timeout 180
[[ $(qm status 210) == 'status: stopped' ]]
qm set 210 --memory 12288
qm start 210
qm config 200 | sed -n '/^name:/p; /^memory:/p'
qm config 210 | sed -n '/^name:/p; /^memory:/p'
HOST
for host in docker atlas; do
  ready=false
  for attempt in $(seq 1 90); do
    if ssh -o BatchMode=yes -o ConnectTimeout=3 "user_l@$host.home.arpa" 'true' 2>/dev/null; then
      ready=true
      break
    fi
    sleep 2
  done
  [[ $ready == true ]]
  ssh -o BatchMode=yes "user_l@$host.home.arpa" 'hostname -f; free -h'
done
ssh -o BatchMode=yes user_l@docker.home.arpa 'test "$(sudo -n systemctl is-enabled puppetserver.service)" = masked; ! sudo -n systemctl is-active --quiet puppetserver.service'
