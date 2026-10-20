#!/usr/bin/env bash
# Keep migrated stacks off even if legacy configuration recreates their units.
set -euo pipefail
[[ $(id -u) == 0 && $(hostname -f) == docker.home.arpa ]] || {
  echo 'Run as root on docker.home.arpa' >&2; exit 1;
}
install -d -m 0755 /etc/docker-stack-retired
units=()
for stack in paperless-ai paperless-ngx send-bills kms; do
  touch "/etc/docker-stack-retired/$stack"
  chmod 0644 "/etc/docker-stack-retired/$stack"
  for suffix in deploy.path deploy.service refresh.timer refresh.service; do
    unit="$stack-$suffix"
    install -d -m 0755 "/etc/systemd/system/$unit.d"
    printf '[Unit]\nConditionPathExists=!/etc/docker-stack-retired/%s\n' "$stack" \
      > "/etc/systemd/system/$unit.d/90-migrated.conf"
    units+=("$unit")
  done
done
for job in paperless-snapshot paperless-correspondent-cleanup; do
  for suffix in service timer; do
    unit="$job.$suffix"
    install -d -m 0755 "/etc/systemd/system/$unit.d"
    printf '[Unit]\nConditionPathExists=!/etc/docker-stack-retired/paperless-ai\n' \
      > "/etc/systemd/system/$unit.d/90-migrated.conf"
    units+=("$unit")
  done
done
systemctl daemon-reload
for unit in "${units[@]}"; do
  if systemctl cat "$unit" >/dev/null 2>&1; then
    systemctl disable --now "$unit"
  fi
done
for stack in paperless-ai send-bills kms; do
  docker --host unix:///var/run/docker.sock compose \
    --env-file /opt/docker/.env --env-file "/opt/docker/$stack/.env" \
    -f "/opt/docker/$stack/docker-compose.yml" down
  [[ -z $(docker --host unix:///var/run/docker.sock ps -q \
    --filter "label=com.docker.compose.project=$stack") ]]
done
for unit in paperless-ai-deploy.service send-bills-deploy.service kms-deploy.service; do
  systemctl start "$unit"
  [[ $(systemctl show "$unit" -p ConditionResult --value) == no ]]
  ! systemctl is-active --quiet "$unit"
done
echo 'Paperless, Send-bills and KMS stopped; deployment guards verified. Volumes preserved.'
