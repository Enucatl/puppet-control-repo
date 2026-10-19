#!/usr/bin/env bash
# Restore only the designated disposable IPA clone; retain it for migration checks.
set -euo pipefail
umask 077
unset DOCKER_HOST DOCKER_CONTEXT
[[ $(id -u) == 0 && $(hostname -f) == atlas.home.arpa &&
   $(cat /etc/openvox-infra/promoted) == openvox-authority:atlas.home.arpa ]] || {
    echo 'Run as root on promoted atlas.home.arpa.' >&2; exit 1;
}
[[ $# == 1 ]] || { echo 'Usage: atlas-freeipa-rehearse.sh BACKUP_DIRECTORY|cleanup' >&2; exit 2; }
docker=(docker --host unix:///var/run/docker.sock)
name=atlas-ipa-rehearsal
volume=atlas-ipa-rehearsal-data
network=atlas-ipa-rehearsal
label=home.arpa.rehearsal=ipa-migration
state=/root/openvox-infra/freeipa
exec 9>/run/lock/atlas-openvox-maintenance.lock
flock -w 300 9

exists() {
    local kind=$1 resource=$2 names
    if [[ $kind == container ]]; then
        names=$("${docker[@]}" container ls --all --format '{{.Names}}') || return 2
    else
        names=$("${docker[@]}" "$kind" ls --format '{{.Name}}') || return 2
    fi
    [[ $'\n'"$names"$'\n' == *$'\n'"$resource"$'\n'* ]]
}
# Check every name before creating or removing anything.
for kind in container volume network; do
    resource=$name
    [[ $kind != volume ]] || resource=$volume
    if exists "$kind" "$resource"; then
        template='{{index .Labels "home.arpa.rehearsal"}}'
        [[ $kind != container ]] || template='{{index .Config.Labels "home.arpa.rehearsal"}}'
        [[ $1 == cleanup && $("${docker[@]}" "$kind" inspect --format "$template" "$resource") == ipa-migration ]] || {
            echo "Refusing existing or unlabelled rehearsal resource: $resource ($kind)" >&2; exit 1;
        }
    else
        [[ $? == 1 ]] || exit 1
    fi
done
if [[ $1 == cleanup ]]; then
    for kind in container volume network; do
        resource=$name
        [[ $kind != volume ]] || resource=$volume
        if exists "$kind" "$resource"; then
            if [[ $kind == container ]]; then "${docker[@]}" container rm --force "$resource" >/dev/null
            else "${docker[@]}" "$kind" rm "$resource" >/dev/null; fi
        else
            [[ $? == 1 ]] || exit 1
        fi
    done
    echo 'Removed labelled IPA rehearsal resources.'
    exit
fi
backup=$(realpath -e -- "$1")
(cd "$backup"; sha256sum --check --strict SHA256SUMS >/dev/null)
root=$(cat /root/openvox-infra/root.sha256)
jq -e --arg root "$root" '.host == "atlas.home.arpa" and .root_sha256 == $root and
    .volume_count == 13 and (.archives | any(.file == "infra_ipa_data.tar.gz"))' \
    "$backup/manifest.json" >/dev/null
digest=$(jq -er '[.archives[] | select(.file == "infra_ipa_data.tar.gz")] |
    select(length == 1) | .[0].sha256 | select(test("^[0-9a-f]{64}$"))' "$backup/manifest.json")
grep -Fqx -- "$digest  infra_ipa_data.tar.gz" "$backup/SHA256SUMS"
image=$(jq -er '.images.ipa | select(test("^sha256:[0-9a-f]{64}$"))' "$backup/manifest.json")
helper=$(jq -er '.images.openvox | select(test("^sha256:[0-9a-f]{64}$"))' "$backup/manifest.json")
identity=$(jq -er '.identity | select(type == "string" and length > 0)' "$state/volume.json")
jq -e --arg identity "$identity" '.volume_identity == $identity' "$state/container-inputs/installer.json" >/dev/null
cmp "$state/container-inputs/root.pem" "$state/root.pem"
jq -e --arg root "$root" '.hostname == "ipa.home.arpa" and .root_sha256 == $root' "$state/ready.json" >/dev/null
"${docker[@]}" image inspect "$image" "$helper" >/dev/null
"${docker[@]}" volume create --label "$label" "$volume" >/dev/null
"${docker[@]}" network create --driver bridge --label "$label" "$network" >/dev/null
# The backup and restore helpers use the same Docker remapping and numeric IDs.
"${docker[@]}" run --rm --pull never --label "$label" \
    --label com.docker.compose.project=atlas-ipa-rehearsal --network none --read-only --user 0:0 \
    --mount "type=volume,src=$volume,dst=/volume" \
    --entrypoint /bin/tar -i "$helper" --numeric-owner --acls --xattrs -xzf - -C /volume \
    < "$backup/infra_ipa_data.tar.gz"
# shellcheck disable=SC2016 # Expand the identity checks inside the helper.
"${docker[@]}" run --rm --pull never --label "$label" \
    --label com.docker.compose.project=atlas-ipa-rehearsal --network none --read-only --user 0:0 \
    --mount "type=volume,src=$volume,dst=/volume,readonly" --entrypoint /bin/sh "$helper" -ec \
    'test "$(cat /volume/.atlas-freeipa-volume)" = "$1";
     test "$(cat /volume/.atlas-freeipa-installed)" = "$1";
     test -s /volume/etc/ipa/default.conf' sh "$identity"
"${docker[@]}" run --detach --pull never --name "$name" --label "$label" \
    --label com.docker.compose.project=atlas-ipa-rehearsal --label com.docker.compose.service=ipa \
    --hostname ipa.home.arpa --runtime sysbox-runc \
    --network "$network" --memory 2g --memory-swap 2g --pids-limit 512 \
    --env IPA_SERVER_IP=no-update --mount "type=volume,src=$volume,dst=/data" \
    --mount "type=bind,src=$state/container-inputs,dst=/bootstrap,readonly" "$image" >/dev/null
timeout 600 "${docker[@]}" exec "$name" bash -ec \
    'while ! test -S /run/dbus/system_bus_socket; do sleep 1; done
     systemctl start ipa.service
     systemctl is-active --quiet ipa.service
     ! systemctl is-active --quiet pki-tomcatd@pki-tomcat.service
     curl --silent --show-error --fail --max-time 15 --cacert /bootstrap/root.pem \
       --resolve ipa.home.arpa:443:127.0.0.1 https://ipa.home.arpa/ipa/ui/ > /dev/null
     LDAPTLS_CACERT=/bootstrap/root.pem ldapsearch -LLL -x -H ldaps://ipa.home.arpa \
       -b "" -s base namingContexts | grep -F "namingContexts: dc=home,dc=arpa" > /dev/null'
timeout 60 "${docker[@]}" exec "$name" python3 -c '
import json
import socket
import subprocess
assert socket.getfqdn() == "ipa.home.arpa", "IPA canonical hostname changed"
resolved = {item[4][0] for item in socket.getaddrinfo("ipa.home.arpa", None, socket.AF_INET)}
local = set(subprocess.check_output(["hostname", "-I"], text=True).split())
assert resolved and resolved <= local, "IPA hostname must resolve to its own interfaces"
with open("/bootstrap/installer.json") as source:
    password = json.load(source)["admin_password"]
subprocess.run(["kinit", "admin"], input=password + "\n", text=True, check=True,
               stdout=subprocess.DEVNULL)
try:
    subprocess.run(["ipa", "server-show", "ipa.home.arpa"], check=True,
                   stdout=subprocess.DEVNULL)
finally:
    subprocess.run(["kdestroy"], check=True)
'
echo 'IPA rehearsal clone ready; no accounts imported. Cleanup explicitly with: atlas-freeipa-rehearse.sh cleanup'
