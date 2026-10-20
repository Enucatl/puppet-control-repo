#!/usr/bin/env bash
# Stop the source bot before handing its closed Telegram session to Atlas.
set -euo pipefail
[[ $(id -u) == 0 && $(hostname -f) == docker.home.arpa ]] || exit 1
install -d -m 0755 /etc/docker-stack-retired
touch /etc/docker-stack-retired/crabberbot
units=()
for suffix in deploy.path deploy.service refresh.timer refresh.service; do
  unit="crabberbot-$suffix"
  install -d -m 0755 "/etc/systemd/system/$unit.d"
  printf '[Unit]\nConditionPathExists=!/etc/docker-stack-retired/crabberbot\n' \
    > "/etc/systemd/system/$unit.d/90-migrated.conf"
  units+=("$unit")
done
systemctl daemon-reload
systemctl disable --now "${units[@]}"
compose=(docker --host unix:///var/run/docker.sock compose \
  --env-file /opt/docker/.env --env-file /opt/docker/crabberbot/.env \
  -f /opt/docker/crabberbot/docker-compose.yml \
  -f /opt/docker/crabberbot/docker-compose.override.yml)
"${compose[@]}" stop crabberbot downloader-worker
python3 "$(dirname "$0")/crabberbot-close-local-api.py"
"${compose[@]}" down
[[ -z $(docker --host unix:///var/run/docker.sock ps -q \
  --filter label=com.docker.compose.project=crabberbot) ]]
systemctl start crabberbot-deploy.service
[[ $(systemctl show crabberbot-deploy.service -p ConditionResult --value) == no ]]
echo 'Source Crabberbot stopped, Telegram session closed, restart guard verified; volumes preserved.'
