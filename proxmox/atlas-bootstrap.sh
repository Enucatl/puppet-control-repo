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
# Reserve Docker's existing numeric mapping, including retries during its transfer.
for mapping in /etc/subuid /etc/subgid; do
    awk -F: '
        ($1 == "user_l" || $1 == "dockremap") && ($2 != 100000 || $3 != 65536) { exit 1 }
        $1 != "user_l" && $1 != "dockremap" && $2 < 165536 && $2 + $3 > 100000 { exit 1 }
    ' "$mapping" || {
        echo "Unexpected or overlapping Docker mapping in $mapping." >&2; exit 1;
    }
done

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
chmod 0755 "$work"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl gnupg git jq openssh-client

cat > "$work/daemon.json" <<'JSON'
{
  "default-address-pools": [
    {"base": "172.17.0.0/12", "size": 20},
    {"base": "2a02:168:6278:a400::/56", "size": 64}
  ],
  "bip": "172.20.0.1/16",
  "experimental": true,
  "fixed-cidr-v6": "2a02:168:6278:a500::/64",
  "ipv6": true,
  "userns-remap": "default",
  "userland-proxy": false,
  "log-driver": "json-file",
  "log-opts": {"max-size": "10m", "max-file": "3"},
  "runtimes": {"sysbox-runc": {"path": "/usr/bin/sysbox-runc"}}
}
JSON
if [[ -e /etc/docker/daemon.json ]]; then
    if [[ "$(jq -cS . /etc/docker/daemon.json)" != "$(jq -cS . "$work/daemon.json")" ]]; then
        jq -e '. == {"userns-remap":"user_l", "userland-proxy":false,
            "log-driver":"json-file", "log-opts":{"max-size":"10m", "max-file":"3"}}' \
            /etc/docker/daemon.json >/dev/null || {
            echo 'Existing Docker settings differ from the known bootstrap; refusing upgrade.' >&2; exit 1;
        }
        resources=$(docker --host unix:///var/run/docker.sock ps -aq &&
            docker --host unix:///var/run/docker.sock volume ls -q &&
            docker --host unix:///var/run/docker.sock network ls --filter type=custom --quiet) || exit 1
        [[ -z $resources ]] || {
            echo 'Bootstrap upgrade requires no containers, volumes or custom networks.' >&2; exit 1;
        }
        systemctl stop docker.service docker.socket
        # Save the target settings now so an interrupted mapping transfer can retry.
        install -m 0644 "$work/daemon.json" /etc/docker/daemon.json
    fi
elif [[ -d /var/lib/docker ]]; then
    echo 'Docker state exists without the expected settings; refusing to remap it.' >&2; exit 1
fi

getent group docker >/dev/null || groupadd --system docker
usermod --append --groups docker user_l
id -u dockremap >/dev/null 2>&1 || useradd --system --user-group --no-create-home \
    --home-dir /nonexistent --shell /usr/sbin/nologin dockremap
if grep -q '^user_l:' /etc/subuid; then usermod --del-subuids 100000-165535 user_l; fi
if grep -q '^user_l:' /etc/subgid; then usermod --del-subgids 100000-165535 user_l; fi
if ! grep -q '^dockremap:' /etc/subuid; then usermod --add-subuids 100000-165535 dockremap; fi
if ! grep -q '^dockremap:' /etc/subgid; then usermod --add-subgids 100000-165535 dockremap; fi

# GitHub's published Ed25519 host key; repository access uses supplied SSH credentials.
cat > "$work/github-known-hosts" <<'KEY'
github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl
KEY
export GIT_SSH_COMMAND="ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$work/github-known-hosts"
checkout() {
    local repository="$1" directory="$2" revision="$3"
    local -a repository_git=(runuser -u user_l -- git -c "safe.directory=$directory")
    if [[ -e $directory ]]; then
        [[ -d $directory/.git && $("${repository_git[@]}" -C "$directory" remote get-url origin) == "$repository" &&
           -z $("${repository_git[@]}" -C "$directory" status --porcelain) ]] || {
            echo "Refusing to overwrite unrelated or modified checkout: $directory" >&2; return 1;
        }
    else
        runuser -u user_l -- git clone "$repository" "$directory"
    fi
    chown -R user_l:docker "$directory"
    "${repository_git[@]}" -C "$directory" fetch --depth 1 origin "$revision"
    [[ $("${repository_git[@]}" -C "$directory" rev-parse FETCH_HEAD) == "$revision" ]]
    "${repository_git[@]}" -C "$directory" checkout --detach "$revision"
}
install -d -o user_l -g docker -m 2775 /opt/docker
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
curl --fail --silent --show-error --location \
    https://github.com/nestybox/sysbox/releases/download/v0.7.1/sysbox-ce_0.7.1.linux_amd64.deb \
    --output "$work/sysbox-ce.deb"
printf '%s  %s\n' 9d6d5484f980d0a17f86c492c1262015c2afb66280bdb97215b79fde6a0261c5 "$work/sysbox-ce.deb" | sha256sum --check -
apt-get install -y "$work/sysbox-ce.deb"
[[ $(dpkg-query -W -f='${Version}' sysbox-ce) == 0.7.1.linux ]]
apt-get install -y \
    docker-ce=5:29.9.1-1~ubuntu.26.04~resolute \
    docker-ce-cli=5:29.9.1-1~ubuntu.26.04~resolute \
    containerd.io=2.4.2-2~ubuntu.26.04~resolute \
    docker-buildx-plugin=0.38.0-1~ubuntu.26.04~resolute \
    docker-compose-plugin=5.6.0-1~ubuntu.26.04~resolute
systemctl enable --now sysbox
systemctl is-active --quiet sysbox sysbox-mgr sysbox-fs
systemctl enable --now docker
docker --host unix:///var/run/docker.sock info --format '{{json .}}' > "$work/docker-info.json"
jq -e '.DockerRootDir == "/var/lib/docker/100000.100000" and
    (.SecurityOptions | any(startswith("name=userns"))) and
    (.Runtimes | has("sysbox-runc"))' "$work/docker-info.json" >/dev/null
docker compose version
docker buildx version
env -i PATH="$PATH" DOCKER_DOMAIN=atlas.home.arpa docker compose \
    --env-file /dev/null -f /opt/docker/puppet-control-repo/docker/docker-compose.yml \
    --profile openvox config --quiet

install -d -m 0755 /var/log/openvox-provisioning
dpkg-query -W docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin sysbox-ce \
    > /var/log/openvox-provisioning/atlas-bootstrap-packages.txt
jq -n --arg control "$CONTROL_REVISION" --arg baseline "$BASELINE_REVISION" \
    --slurpfile docker "$work/docker-info.json" \
    '{control_revision:$control, baseline_revision:$baseline, userns_base:100000,
      docker_version:$docker[0].ServerVersion, storage_driver:$docker[0].Driver}' \
    > /var/log/openvox-provisioning/atlas-bootstrap.json
echo "Atlas deployment tools ready; infrastructure checkout: $CONTROL_REVISION"
