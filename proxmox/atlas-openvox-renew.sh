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
wait_for_health() {
    local deadline=$((SECONDS + 300)) status
    while (( SECONDS < deadline )); do
        status=$("${docker[@]}" inspect --format '{{.State.Running}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' infra-openvox-1)
        case "$status" in
            'true healthy') return 0 ;;
            'true starting'|'true unhealthy') sleep 2 ;;
            *) echo 'Established OpenVox container is not running with a health check.' >&2; return 1 ;;
        esac
    done
    echo 'Timed out waiting for the established OpenVox container to become healthy.' >&2
    return 1
}

running=$("${docker[@]}" inspect --format '{{.State.Running}}' infra-openvox-1)
case "$running" in
    true) ;;
    false) "${docker[@]}" start infra-openvox-1 >/dev/null ;;
    *) echo 'Renewal requires the established OpenVox container.' >&2; exit 1 ;;
esac
result=$("${docker[@]}" "${helper[@]}" renew)
jq -e '(.reload_required | type) == "boolean"' <<<"$result" >/dev/null
if jq -e '.reload_required' <<<"$result" >/dev/null; then
    "${docker[@]}" restart --time 60 infra-openvox-1 >/dev/null
fi
wait_for_health
"${docker[@]}" "${helper[@]}" verify
