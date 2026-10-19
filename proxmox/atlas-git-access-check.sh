#!/bin/bash
set -euo pipefail

[[ $(hostname -f) == atlas.home.arpa ]] || {
    echo 'Run on atlas.home.arpa after building infra-openvox:24001.' >&2; exit 1;
}
docker=(docker --host unix:///var/run/docker.sock)
bare=/opt/git/openvox/control.git
revision=$(git --git-dir="$bare" rev-parse production)
"${docker[@]}" run --rm -i --network none --read-only --user 999:999 \
    --cap-drop ALL --security-opt no-new-privileges=true \
    --tmpfs /tmp:rw,nosuid,nodev,uid=999,gid=999,mode=0700 \
    --tmpfs /git:rw,nosuid,nodev,uid=999,gid=999,mode=0700 \
    --tmpfs /var/cache/r10k:rw,nosuid,nodev,uid=999,gid=999,mode=0700 \
    --tmpfs /etc/puppetlabs/code:rw,nosuid,nodev,uid=999,gid=999,mode=0700 \
    --mount "type=bind,source=$bare,target=/git/control.git,readonly" \
    --mount type=bind,source=/opt/docker/puppet-control-repo/docker/openvox/infra-r10k.yaml,target=/infra/infra-r10k.yaml,readonly \
    --entrypoint /bin/bash infra-openvox:24001 -eus -- "$revision" >&2 <<'CONTAINER'
test "$(id -u):$(id -g)" = 999:999
awk '$1 == 0 && $2 == 100000 && $3 == 65536 { found=1 } END { exit !found }' /proc/self/uid_map
awk '$1 == 0 && $2 == 100000 && $3 == 65536 { found=1 } END { exit !found }' /proc/self/gid_map
grep -Eq " /git/control.git ro[, ]" /proc/self/mountinfo
test "$(git --git-dir=/git/control.git rev-parse production)" = "$1"
if touch /git/control.git/write-probe; then
    echo "Source repository unexpectedly writable" >&2; exit 1
fi
printf "ok\n" > /git/deploy.status
/opt/puppetlabs/puppet/bin/r10k deploy environment production --config /infra/infra-r10k.yaml
test "$(git -C /etc/puppetlabs/code/environments/production rev-parse HEAD)" = "$1"
echo "Remapped read-only source and r10k checkout passed"
CONTAINER

jq -n --arg date "$(date --iso-8601=seconds)" --arg revision "$revision" \
    --arg image "$("${docker[@]}" image inspect infra-openvox:24001 --format '{{.Id}}')" \
    '{verified_at:$date, production_revision:$revision, image_id:$image,
      container_uid_gid:"999:999", host_uid_gid:"100999:100999",
      userns_remap_verified:true, source_read_only:true, source_write_denied:true,
      deployment_status_writable:true, r10k_checkout_passed:true,
      service_started:false, authority_initialized:false}'
