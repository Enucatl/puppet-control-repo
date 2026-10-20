#!/usr/bin/env bash
# Retire only Grafana; keep the legacy Loki service and deployment active.
set -euo pipefail
[[ $(id -u) == 0 && $(hostname -f) == docker.home.arpa ]] || exit 1
for unit in grafana-loki-deploy.service grafana-loki-refresh.service; do
  systemctl stop "$unit"
  install -d -m 0755 "/etc/systemd/system/$unit.d"
  cat > "/etc/systemd/system/$unit.d/90-loki-only.conf" <<'UNIT'
[Service]
ExecStart=
ExecStart=/usr/bin/docker compose pull loki
ExecStart=/usr/bin/docker compose up --force-recreate --wait --wait-timeout 300 loki
UNIT
done
systemctl daemon-reload
docker --host unix:///var/run/docker.sock compose \
  --env-file /opt/docker/.env --env-file /opt/docker/grafana-loki/.env \
  -f /opt/docker/grafana-loki/docker-compose.yml stop grafana
docker --host unix:///var/run/docker.sock compose \
  --env-file /opt/docker/.env --env-file /opt/docker/grafana-loki/.env \
  -f /opt/docker/grafana-loki/docker-compose.yml rm -f grafana
[[ -z $(docker --host unix:///var/run/docker.sock ps -q \
  --filter label=com.docker.compose.project=grafana-loki \
  --filter label=com.docker.compose.service=grafana) ]]
[[ $(docker --host unix:///var/run/docker.sock inspect grafana-loki-loki-1 \
  --format '{{.State.Running}}') == true ]]
for unit in grafana-loki-deploy.service grafana-loki-refresh.service; do
  systemctl show "$unit" -p ExecStart --value | grep -q 'up .* loki'
done
echo 'Old Grafana removed; volumes preserved; Loki running and automation restricted to Loki.'
