#!/usr/bin/env bash
set -euo pipefail
umask 077
unset DOCKER_HOST DOCKER_CONTEXT

[[ $# == 1 && $1 == /scratch/vault/* ]] || {
    echo 'Usage: atlas-infra-backup.sh /scratch/vault/NEW_BACKUP_DIRECTORY' >&2; exit 2;
}
[[ $(id -u) == 0 && $(hostname -f) == atlas.home.arpa ]] || {
    echo 'Run as root on atlas.home.arpa.' >&2; exit 1;
}
[[ $(cat /etc/openvox-infra/promoted) == openvox-authority:atlas.home.arpa ]] || {
    echo 'Backups require the promoted Atlas authority.' >&2; exit 1;
}
state=/root/openvox-infra
jq -e '(.root_sha256 | test("^[0-9a-f]{64}$")) and
    (.control_commit | test("^[0-9a-f]{40}$"))' "$state/ready.json" >/dev/null
output=$(realpath -m -- "$1")
[[ $output == /scratch/vault/* && ! -e $output && ! -L $output ]] || {
    echo 'Backup output must be a new directory under /scratch/vault.' >&2; exit 1;
}
exec 9>/run/lock/atlas-openvox-maintenance.lock
flock -w 300 9

source=/opt/docker/puppet-control-repo
baseline=/opt/docker/compose-security-baseline
bare=/opt/git/openvox/control.git
docker=(docker --host unix:///var/run/docker.sock)
compose=(env -i "PATH=$PATH" DOCKER_DOMAIN=home.arpa "${docker[@]}" compose
    --env-file /dev/null -p infra
    -f "$source/docker/docker-compose.yml"
    -f "$source/docker/openvox/infra-vault.override.yaml")
volumes=(infra_openvox_{ssl,ca,code,data,gems,git,r10k_cache,trust}
    infra_vault_data infra_vault_logs infra_certificates infra_vault_unseal)
containers=(infra-openvox-1 infra-vault-1 infra-vault-unsealer-1)
ipa_running=false
ipa_image=''
ipa_source=''
ipa_compose=(env -i "PATH=$PATH" "${docker[@]}" compose --env-file /dev/null -p infra
    -f "$source/docker/freeipa/atlas-compose.yaml")
if [[ -e $state/freeipa ]]; then
    jq -e --arg root "$(jq -r .root_sha256 "$state/ready.json")" \
        '.hostname == "ipa.home.arpa" and .root_sha256 == $root' \
        "$state/freeipa/ready.json" >/dev/null
    ipa_identity=$(jq -er '.identity | select(type == "string" and length > 0)' \
        "$state/freeipa/volume.json")
    volumes+=(infra_ipa_data)
fi

# Refuse missing state before Docker could create an empty replacement volume.
for volume in "${volumes[@]}"; do
    [[ $("${docker[@]}" volume inspect --format '{{index .Labels "home.arpa.authority"}}' "$volume") == atlas ]] || {
        echo "Missing or unmanaged authority volume: $volume" >&2; exit 1;
    }
done
records=$("${docker[@]}" inspect --format '{{.Image}} {{.State.Running}}' "${containers[@]}")
images=()
while read -r image running; do
    [[ $image =~ ^sha256:[0-9a-f]{64}$ && $running == true ]] || {
        echo 'All three established infrastructure services must be running.' >&2; exit 1;
    }
    images+=("$image")
done <<<"$records"
[[ ${#images[@]} == 3 ]]
vault_reference=$("${compose[@]}" config --format json | jq -er '.services.vault.image')
[[ $vault_reference =~ ^hashicorp/vault@sha256:[0-9a-f]{64}$ &&
   $("${docker[@]}" image inspect --format '{{.Id}}' "$vault_reference") == "${images[1]}" ]] || {
    echo 'Running Vault does not match the configured immutable image.' >&2; exit 1;
}
if [[ -n ${ipa_identity:-} ]]; then
    [[ $("${docker[@]}" run --rm --network none --read-only --user 0:0 \
        --cap-drop ALL --cap-add DAC_READ_SEARCH \
        --mount type=volume,src=infra_ipa_data,dst=/volume,readonly \
        --entrypoint /bin/cat "${images[0]}" /volume/.atlas-freeipa-volume) == "$ipa_identity" ]] || {
        echo 'IPA volume identity changed; restore original state.' >&2; exit 1;
    }
    read -r ipa_image ipa_running < <("${docker[@]}" inspect --format \
        '{{.Image}} {{.State.Running}}' infra-ipa-1)
    [[ $ipa_image =~ ^sha256:[0-9a-f]{64}$ && $ipa_running =~ ^(true|false)$ ]]
    ipa_source=$(sed -n 's/^FROM //p' "$source/docker/freeipa/atlas.Dockerfile")
    [[ $ipa_source =~ ^ghcr.io/enucatl/freeipa-server@sha256:[0-9a-f]{64}$ ]]
    images+=("$ipa_image")
    if [[ $ipa_running == true ]]; then containers+=(infra-ipa-1); fi
fi
for repository in "$source" "$baseline"; do
    [[ -z $(git -c "safe.directory=$repository" -C "$repository" status --porcelain) ]] || {
        echo "Commit or preserve checkout changes before backup: $repository" >&2; exit 1;
    }
done
[[ $(git -c "safe.directory=$bare" --git-dir="$bare" rev-parse --is-bare-repository) == true ]]
if [[ ! -x $bare/hooks/pre-receive ]] ||
    ! cmp -s "$source/docker/openvox/infra-host-pre-receive" "$bare/hooks/pre-receive"; then
    echo 'Install the managed receive guard through publishing before backup.' >&2; exit 1;
fi
control_revision=$(git -c "safe.directory=$source" -C "$source" rev-parse HEAD)
baseline_revision=$(git -c "safe.directory=$baseline" -C "$baseline" rev-parse HEAD)
install -d -m 0700 /scratch/vault
mkdir -m 0700 "$output"

stopped=true
wait_healthy() {
    local deadline=$((SECONDS + 300)) records
    while (( SECONDS < deadline )); do
        records=$("${docker[@]}" inspect --format \
            '{{.State.Running}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' "${containers[@]}") || return 1
        if [[ $(printf '%s\n' "$records" | sort -u) == 'true healthy' ]]; then return 0; fi
        if [[ $records == *false* ]]; then
            echo 'An established Atlas service stopped during recovery.' >&2; return 1
        fi
        sleep 2
    done
    echo 'Timed out waiting for healthy Atlas services.' >&2
    return 1
}
start_services() {
    local status=0
    "${compose[@]}" start vault vault-unsealer openvox >/dev/null || status=1
    if [[ $ipa_running == true ]]; then "${ipa_compose[@]}" start ipa >/dev/null || status=1; fi
    wait_healthy || status=1
    return "$status"
}
restore_services() {
    local status=$?
    trap - EXIT
    if [[ $stopped == true ]]; then
        start_services || {
            echo 'Backup failed to restore healthy established Atlas services; restore them explicitly.' >&2; exit 1;
        }
    fi
    exit "$status"
}
trap restore_services EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if [[ $ipa_running == true ]]; then "${ipa_compose[@]}" stop ipa >/dev/null; fi
"${compose[@]}" stop openvox vault-unsealer vault >/dev/null

for volume in "${volumes[@]}"; do
    "${docker[@]}" run --rm --network none --read-only --user 0:0 \
        --cap-drop ALL --cap-add DAC_READ_SEARCH \
        --mount "type=volume,src=$volume,dst=/volume,readonly" \
        --entrypoint /bin/tar "${images[0]}" \
        --numeric-owner --acls --xattrs -czf - -C /volume . > "$output/$volume.tar.gz"
done
tar --numeric-owner --acls --xattrs -czf "$output/credentials.tar.gz" -C /root openvox-infra
host_config=(etc/docker/daemon.json etc/subuid etc/subgid etc/openvox-infra/promoted)
for unit in atlas-openvox-renew.service atlas-openvox-renew.timer; do
    if [[ -f /etc/systemd/system/$unit ]]; then host_config+=("etc/systemd/system/$unit"); fi
done
tar --numeric-owner --acls --xattrs -czf "$output/host-config.tar.gz" -C / "${host_config[@]}"
tar --numeric-owner --acls --xattrs -czf "$output/control.git.tar.gz" -C /opt/git/openvox control.git
git -c "safe.directory=$source" -C "$source" archive --format=tar.gz "$control_revision" > "$output/control.tar.gz"
git -c "safe.directory=$baseline" -C "$baseline" archive --format=tar.gz "$baseline_revision" > "$output/hardening.tar.gz"
start_services
stopped=false
# Image IDs are immutable; export them after the authorities are running again.
"${docker[@]}" image save "${images[@]}" | gzip -1 > "$output/images.tar.gz"

(cd "$output"; sha256sum -- *.tar.gz) > "$output/SHA256SUMS"
jq -n --arg control "$control_revision" --arg baseline "$baseline_revision" \
    --arg root "$(jq -r .root_sha256 "$state/ready.json")" \
    --arg openvox "${images[0]}" --arg vault "${images[1]}" --arg unsealer "${images[2]}" \
    --arg ipa "$ipa_image" --arg ipa_source "$ipa_source" \
    --argjson volume_count "${#volumes[@]}" --rawfile checksums "$output/SHA256SUMS" \
    '($checksums | split("\n") | map(select(length > 0) |
        capture("^(?<sha256>[0-9a-f]{64})  (?<file>.+)$"))) as $archives |
    {host:"atlas.home.arpa", control_revision:$control, baseline_revision:$baseline,
     root_sha256:$root, images:{openvox:$openvox, vault:$vault, unsealer:$unsealer},
     volume_count:$volume_count, archive_count:($archives|length), archives:$archives}
     + if $ipa != "" then {images:{openvox:$openvox, vault:$vault, unsealer:$unsealer, ipa:$ipa},
         ipa_source_reference:$ipa_source} else {} end' \
    > "$output/manifest.json"
manifest_hash=$(sha256sum "$output/manifest.json")
printf 'Backup ready: %s (%s volumes; manifest SHA256 %s)\n' \
    "$output" "${#volumes[@]}" "${manifest_hash%% *}"
