#!/usr/bin/env bash
# Quiesce one migrated background stack and block automatic source restarts.
set -euo pipefail
[[ $(id -u) == 0 && $(hostname -f) == docker.home.arpa && $# == 1 ]] || exit 1
stack=$1
case "$stack" in protonmail-bridge|rss-to-telegram-bot|tor-i2pd|airflow) ;; *) exit 1 ;; esac
install -d -m 0755 /etc/docker-stack-retired
touch "/etc/docker-stack-retired/$stack"
units=()
for suffix in deploy.path deploy.service refresh.timer refresh.service; do
  unit="$stack-$suffix"
  install -d -m 0755 "/etc/systemd/system/$unit.d"
  printf '[Unit]\nConditionPathExists=!/etc/docker-stack-retired/%s\n' "$stack" \
    > "/etc/systemd/system/$unit.d/90-migrated.conf"
  units+=("$unit")
done
if [[ $stack == airflow ]]; then
  for job in exam cyber-analyst operations-analyst podcast-statistics; do
    for suffix in service timer; do
      unit="automation-$job.$suffix"
      install -d -m 0755 "/etc/systemd/system/$unit.d"
      printf '[Unit]\nConditionPathExists=!/etc/docker-stack-retired/airflow\n' \
        > "/etc/systemd/system/$unit.d/90-migrated.conf"
      units+=("$unit")
    done
  done
fi
systemctl daemon-reload
systemctl disable --now "${units[@]}"
compose=(docker --host unix:///var/run/docker.sock compose \
  --env-file /opt/docker/.env --env-file "/opt/docker/$stack/.env" \
  -f "/opt/docker/$stack/docker-compose.yml")
if [[ $stack == tor-i2pd ]]; then
  # DDNS still serves TeamSpeak and torrents on Docker.
  "${compose[@]}" stop i2pd
  "${compose[@]}" rm -f i2pd
  [[ -z $(docker --host unix:///var/run/docker.sock ps -q \
    --filter "label=com.docker.compose.project=$stack" \
    --filter label=com.docker.compose.service=i2pd) ]]
else
  "${compose[@]}" down
  [[ -z $(docker --host unix:///var/run/docker.sock ps -q \
    --filter "label=com.docker.compose.project=$stack") ]]
fi
systemctl start "$stack-deploy.service"
[[ $(systemctl show "$stack-deploy.service" -p ConditionResult --value) == no ]]
printf '%s stopped; deployment guard verified; volumes preserved.\n' "$stack"
