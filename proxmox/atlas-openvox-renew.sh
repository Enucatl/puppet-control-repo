#!/usr/bin/env bash
set -euo pipefail
umask 077
unset DOCKER_HOST DOCKER_CONTEXT

[[ $# == 0 ]] || { echo 'Usage: atlas-openvox-renew.sh' >&2; exit 2; }
[[ $(id -u) == 0 && $(hostname -f) == atlas.home.arpa ]] || {
    echo 'Run as root on atlas.home.arpa.' >&2; exit 1;
}
[[ $(cat /etc/openvox-infra/promoted) == openvox-authority:atlas.home.arpa &&
   -s /root/openvox-infra/ready.json ]] || {
    echo 'Renewal requires established, promoted Atlas state.' >&2; exit 1;
}
exec 9>/run/lock/atlas-openvox-maintenance.lock
flock -w 300 9

docker=(docker --host unix:///var/run/docker.sock)
helper=(exec infra-openvox-1 /opt/puppetlabs/puppet/bin/ruby /infra/infra-renew.rb)
result=$("${docker[@]}" "${helper[@]}" renew)
jq -e '(.reload_required | type) == "boolean"' <<<"$result" >/dev/null
if jq -e '.reload_required' <<<"$result" >/dev/null; then
    "${docker[@]}" restart --time 60 infra-openvox-1 >/dev/null
    env -i "PATH=$PATH" DOCKER_DOMAIN=home.arpa "${docker[@]}" compose \
        --env-file /dev/null -p infra \
        -f /opt/docker/puppet-control-repo/docker/docker-compose.yml \
        -f /opt/docker/puppet-control-repo/docker/openvox/infra-vault.override.yaml \
        up -d --wait --wait-timeout 300 --no-deps openvox
fi
"${docker[@]}" "${helper[@]}" verify
