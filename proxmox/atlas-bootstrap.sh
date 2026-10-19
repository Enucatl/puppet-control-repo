#!/bin/bash
set -euo pipefail

[[ $# == 1 && $1 =~ ^[0-9a-f]{40}$ ]] || {
    echo "Usage: $0 CONTROL_COMMIT_SHA (run as root on Atlas)" >&2; exit 1;
}
CONTROL_REVISION="$1"
BASELINE_REVISION=41c57257d33d471e2ae7cc7264938944c67434fd
[[ $(id -u) == 0 && $(hostname -f) == atlas.home.arpa ]] || {
    echo 'Run as root on atlas.home.arpa.' >&2; exit 1;
}
. /etc/os-release
[[ $ID:$VERSION_ID:$(dpkg --print-architecture) == ubuntu:26.04:amd64 ]] || {
    echo 'Atlas bootstrap requires Ubuntu 26.04 amd64.' >&2; exit 1;
}
# Keep the existing 100000:100000 container ownership without allocating new ranges.
for mapping in /etc/subuid /etc/subgid; do
    [[ $(sed -n '/^user_l:/p' "$mapping") == user_l:100000:65536 ]] || {
        echo "Unexpected user_l mapping in $mapping; refusing Docker setup." >&2; exit 1;
    }
done

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl gnupg git jq openssh-client

cat > "$work/daemon.json" <<'JSON'
{
  "userns-remap": "user_l",
  "userland-proxy": false,
  "log-driver": "json-file",
  "log-opts": {"max-size": "10m", "max-file": "3"}
}
JSON
if [[ -e /etc/docker/daemon.json ]]; then
    [[ $(jq -cS . /etc/docker/daemon.json) == $(jq -cS . "$work/daemon.json") ]] || {
        echo 'Existing Docker settings differ; refusing to change the daemon or its state.' >&2; exit 1;
    }
elif [[ -d /var/lib/docker ]]; then
    echo 'Docker state exists without the expected settings; refusing to remap it.' >&2; exit 1
fi

# GitHub's published Ed25519 host key; repository access uses supplied SSH credentials.
cat > "$work/github-known-hosts" <<'KEY'
github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl
KEY
export GIT_SSH_COMMAND="ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$work/github-known-hosts"
checkout() {
    local repository="$1" directory="$2" revision="$3"
    if [[ -e $directory ]]; then
        [[ -d $directory/.git && $(git -C "$directory" remote get-url origin) == "$repository" &&
           -z $(git -C "$directory" status --porcelain) ]] || {
            echo "Refusing to overwrite unrelated or modified checkout: $directory" >&2; return 1;
        }
    else
        git clone "$repository" "$directory"
    fi
    git -C "$directory" fetch --depth 1 origin "$revision"
    [[ $(git -C "$directory" rev-parse FETCH_HEAD) == "$revision" ]]
    git -C "$directory" checkout --detach "$revision"
}
install -d -m 0755 /opt/docker
checkout git@github.com:Enucatl/puppet-control-repo.git /opt/docker/puppet-control-repo "$CONTROL_REVISION"
checkout git@github.com:Enucatl/docker-compose-security-baseline.git /opt/docker/compose-security-baseline "$BASELINE_REVISION"

curl --fail --silent --show-error --location https://download.docker.com/linux/ubuntu/gpg --output "$work/docker.asc"
fingerprints=$(gpg --batch --show-keys --with-colons "$work/docker.asc" |
    awk -F: '$1 == "pub" { primary = 1; next } primary && $1 == "fpr" { print $10; primary = 0 }')
[[ $fingerprints == 9DC858229FC7DD38854AE2D88D81803C0EBFCD88 ]] || {
    echo 'Docker archive fingerprint mismatch.' >&2; exit 1;
}
install -d -m 0755 /etc/apt/keyrings /etc/docker
install -m 0644 "$work/docker.asc" /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.sources <<'SOURCES'
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: resolute
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
SOURCES
install -m 0644 "$work/daemon.json" /etc/docker/daemon.json
apt-get update
apt-get install -y \
    docker-ce=5:29.9.1-1~ubuntu.26.04~resolute \
    docker-ce-cli=5:29.9.1-1~ubuntu.26.04~resolute \
    containerd.io=2.4.2-2~ubuntu.26.04~resolute \
    docker-buildx-plugin=0.38.0-1~ubuntu.26.04~resolute \
    docker-compose-plugin=5.6.0-1~ubuntu.26.04~resolute
systemctl enable --now docker
docker --host unix:///var/run/docker.sock info --format '{{json .}}' > "$work/docker-info.json"
jq -e '.DockerRootDir == "/var/lib/docker/100000.100000" and
    (.SecurityOptions | any(startswith("name=userns")))' "$work/docker-info.json" >/dev/null
docker compose version
docker buildx version
env -i PATH="$PATH" DOCKER_DOMAIN=atlas.home.arpa docker compose \
    --env-file /dev/null -f /opt/docker/puppet-control-repo/docker/docker-compose.yml \
    --profile openvox config --quiet

install -d -m 0755 /var/log/openvox-provisioning
dpkg-query -W docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin \
    > /var/log/openvox-provisioning/atlas-bootstrap-packages.txt
jq -n --arg control "$CONTROL_REVISION" --arg baseline "$BASELINE_REVISION" \
    --slurpfile docker "$work/docker-info.json" \
    '{control_revision:$control, baseline_revision:$baseline, userns_base:100000,
      docker_version:$docker[0].ServerVersion, storage_driver:$docker[0].Driver}' \
    > /var/log/openvox-provisioning/atlas-bootstrap.json
echo "Atlas deployment tools ready; infrastructure checkout: $CONTROL_REVISION"
