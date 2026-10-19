#!/usr/bin/env bash
# Remove disposable provisioning ownership before Atlas holds an authority.
set -euo pipefail

[[ $# == 0 ]] || { echo 'Usage: atlas-promote.sh (fixed Atlas VM 210)' >&2; exit 2; }
[[ $(id -u) == 0 ]] || { echo 'Run on Proxmox as root.' >&2; exit 1; }
owner='openvox-provisioning:docker:atlas.home.arpa'
marker='openvox-authority:atlas.home.arpa'
config=$(qm config 210)
description=$(sed -n 's/^description: //p' <<<"$config")
network=$(sed -n 's/^net0: //p' <<<"$config")
mac=${network%%,*}
mac=${mac#*=}
encoded_owner=$(jq -rn --arg owner "$owner" '$owner | @uri')
encoded_marker=$(jq -rn --arg marker "$marker" '$marker | @uri')
[[ $(sed -n 's/^name: //p' <<<"$config") == atlas &&
   ${mac,,} == bc:24:11:4e:a4:9e &&
   ( $description == "$owner" || $description == "$encoded_owner" ||
     $description == "$marker" || $description == "$encoded_marker" ) ]] || {
    echo 'VM 210 name, MAC or ownership does not match Atlas.' >&2; exit 1;
}
if grep -q '^template: 1$' <<<"$config"; then
    echo 'Refusing to promote a template.' >&2; exit 1
fi

# Protect against rebuild first; a failed guest write can safely be retried.
qm set 210 --description "$marker"
result=$(qm guest exec 210 -- sh -eu -c '
    install -d -o root -g root -m 0755 /etc/openvox-infra
    printf "%s\n" "openvox-authority:atlas.home.arpa" > /etc/openvox-infra/promoted
    chown root:root /etc/openvox-infra/promoted
    chmod 0644 /etc/openvox-infra/promoted
')
jq -e '(.exited == 1 or .exited == true) and .exitcode == 0' <<<"$result" >/dev/null || {
    echo 'Atlas is protected against rebuild, but guest promotion did not complete successfully; retry promotion.' >&2
    exit 1
}
echo 'Atlas VM 210 promoted to an authoritative host.'
