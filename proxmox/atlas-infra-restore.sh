#!/usr/bin/env bash
# Restore established authorities on a fresh replacement Atlas; never initialize.
set -euo pipefail
umask 077
unset DOCKER_HOST DOCKER_CONTEXT COMPOSE_FILE COMPOSE_ENV_FILES
[[ $# == 2 && $2 =~ ^[0-9a-f]{64}$ ]] || {
    echo 'Usage: atlas-infra-restore.sh BACKUP_DIRECTORY EXPECTED_MANIFEST_SHA256' >&2; exit 2;
}
[[ $(id -u) == 0 && $(hostname -f) == atlas.home.arpa ]] || {
    echo 'Run as root on replacement atlas.home.arpa.' >&2; exit 1;
}
script=$(cd -- "$(dirname -- "$0")" && pwd)
kit=$(realpath -e -- "$1")
work=/root/openvox-recovery
source=/opt/docker/puppet-control-repo
baseline=/opt/docker/compose-security-baseline
docker=(docker --host unix:///var/run/docker.sock)
printf '%s  %s\n' "$2" "$kit/manifest.json" | sha256sum --check --status
jq -e '.host == "atlas.home.arpa" and (.volume_count == 12 or .volume_count == 13) and
    (.root_sha256 | test("^[0-9a-f]{64}$")) and
    (.archives | all(.file | test("^[a-zA-Z0-9_.-]+[.]tar[.]gz$")))' "$kit/manifest.json" >/dev/null
# Authenticate the checksum list against the separately verified manifest.
expected=$(jq -r '.archives[] | "\(.sha256)  \(.file)"' "$kit/manifest.json" | sort)
[[ $(sort "$kit/SHA256SUMS") == "$expected" ]]
(cd "$kit"; sha256sum --check --strict SHA256SUMS >/dev/null)
mapfile -t volumes < <(jq -r '.archives[].file | select(startswith("infra_")) | sub("[.]tar[.]gz$"; "")' "$kit/manifest.json")
[[ ${#volumes[@]} == $(jq -r .volume_count "$kit/manifest.json") ]]
for path in "$work" "$source" "$baseline" /root/openvox-infra /opt/git/openvox/control.git; do
    [[ ! -e $path && ! -L $path ]] || { echo "Refuse existing recovery target: $path" >&2; exit 1; }
done
existing=$("${docker[@]}" volume ls --format '{{.Name}}')
for volume in "${volumes[@]}"; do
    [[ $volume =~ ^infra_(openvox_(ssl|ca|code|data|gems|git|r10k_cache|trust)|vault_(data|logs|unseal)|certificates|ipa_data)$ ]]
    [[ $'\n'"$existing"$'\n' != *$'\n'"$volume"$'\n'* ]] || {
        echo "Refuse existing authority volume: $volume" >&2; exit 1;
    }
done
mkdir -m 0700 "$work"
tar -xzf "$kit/host-config.tar.gz" -C "$work" etc/docker/daemon.json etc/subuid etc/subgid
# Host-owned bind inputs must retain the same mapping; restore volumes through Docker.
[[ $(jq -r '.["userns-remap"]' "$work/etc/docker/daemon.json") == $(jq -r '.["userns-remap"]' /etc/docker/daemon.json) ]]
remap=$(jq -r '.["userns-remap"] // empty' "$work/etc/docker/daemon.json")
[[ -n $remap ]]
[[ $remap != default ]] || remap=dockremap
IFS=: read -r remap_user remap_group <<< "$remap"
for pair in "subuid:$remap_user" "subgid:${remap_group:-$remap_user}"; do
    name=${pair%%:*}
    account=${pair#*:}
    saved=$(awk -F: -v account="$account" '$1 == account' "$work/etc/$name")
    [[ -n $saved && $saved == "$(awk -F: -v account="$account" '$1 == account' "/etc/$name")" ]] || {
        echo "Restore saved $account $name mapping before proceeding." >&2; exit 1;
    }
done
python3 "$script/atlas-repair-oci.py" "$kit" "$work/oci-descriptors.json" |
    ctr -n moby images import --digests --no-unpack - > "$work/image-import.log"
helper=docker.io/atlas-restore-oci/openvox:archived
for volume in "${volumes[@]}"; do
    "${docker[@]}" volume create --label home.arpa.authority=atlas "$volume" >/dev/null
    "${docker[@]}" run --rm --pull never --network none --read-only --user 0:0 -i \
        --mount "type=volume,src=$volume,dst=/volume" --entrypoint /bin/tar "$helper" \
        --numeric-owner --acls --xattrs -xzf - -C /volume < "$kit/$volume.tar.gz"
done
install -d -m 0755 "$source" "$baseline" /opt/git/openvox
for pair in "control:$source" "hardening:$baseline" "control.git:/opt/git/openvox" "credentials:/root"; do
    tar --numeric-owner --acls --xattrs -xzf "$kit/${pair%%:*}.tar.gz" -C "${pair#*:}"
done
# This includes the saved native enrollment, SSH identity and issued website keys.
tar --numeric-owner --acls --xattrs -xzf "$kit/host-config.tar.gz" -C / \
    --exclude=etc/docker/daemon.json --exclude=etc/subuid --exclude=etc/subgid
jq -e --arg root "$(jq -r .root_sha256 "$kit/manifest.json")" '.root_sha256 == $root' /root/openvox-infra/ready.json >/dev/null

# Use genuine imported manifests offline; never forge the missing multiarch pin.
compose=(env -i "PATH=$PATH" DOCKER_DOMAIN=home.arpa "${docker[@]}" compose --env-file /dev/null -p infra)
"${compose[@]}" --profile openvox -f "$source/docker/docker-compose.yml" \
    -f "$source/docker/openvox/infra-vault.override.yaml" config --format json |
    jq '.services |= with_entries(select(.key == "openvox" or .key == "vault" or .key == "vault-unsealer")) |
        .services.openvox.image = "docker.io/atlas-restore-oci/openvox:archived" |
        .services.vault.image = "docker.io/atlas-restore-oci/vault:archived" |
        .services["vault-unsealer"].image = "docker.io/atlas-restore-oci/unsealer:archived" |
        .services[] |= del(.build, .profiles) |
        .networks |= with_entries(select(.key == "infra")) |
        [.services[].volumes[] | select(.type == "volume") | .source] as $used |
        .volumes |= with_entries(select(.key as $key | $used | index($key)))' > "$work/infra.json"
if [[ ${#volumes[@]} == 13 ]]; then
    "${compose[@]}" -f "$source/docker/freeipa/atlas-compose.yaml" config --format json |
        jq '.services.ipa.image = "docker.io/atlas-restore-oci/ipa:archived" | del(.services.ipa.build)' > "$work/ipa.json"
fi
printf 'Established state restored. Follow docs/atlas-recovery.md to start and validate it.\n'
