#!/bin/bash
set -euo pipefail
[[ $(id -u) == 0 && $(hostname -f) == aperture.home.arpa ]] || {
    echo 'Run as root on aperture.home.arpa.' >&2; exit 1;
}
bash /usr/local/bin/configure-puppet.sh --apply aperture.home.arpa openvox.home.arpa
systemctl start qemu-guest-agent
systemctl set-default graphical.target
systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
