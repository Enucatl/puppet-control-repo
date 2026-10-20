#!/usr/bin/env bash
# Export a stable source-volume archive without stopping Docker containers.
set -euo pipefail
[[ $(id -u) == 0 && $(hostname -f) == proxmox.home.arpa ]] || {
  echo 'Run as root on proxmox.home.arpa' >&2; exit 1;
}
exec 9>/run/lock/atlas-observability-export.lock
flock -n 9
source_volume=rpool/data/vm-200-disk-0
snapshot="${source_volume}@atlas-observability-$(date +%s)-$$"
clone="rpool/data/atlas-observability-export-$$"
work=$(mktemp -d /run/atlas-observability.XXXXXX)
frozen=false
snap_created=false
clone_created=false
loop=
cleanup() {
  result=$?
  trap - EXIT
  if $frozen; then qm guest cmd 200 fsfreeze-thaw >&2 || result=1; fi
  if mountpoint -q "$work"; then umount "$work" || result=1; fi
  if [[ -n $loop ]]; then losetup -d "$loop" || result=1; fi
  if $clone_created; then zfs destroy "$clone" || result=1; fi
  if $snap_created; then zfs destroy "$snapshot" || result=1; fi
  rmdir "$work" || result=1
  exit "$result"
}
trap cleanup EXIT
[[ $(qm config 200 | sed -n 's/^scsi0: \([^,]*\).*/\1/p') == local-zfs:vm-200-disk-0 ]]
[[ $(qm guest cmd 200 fsfreeze-status) == thawed ]]
frozen=true
qm guest cmd 200 fsfreeze-freeze >&2
zfs snapshot "$snapshot"
snap_created=true
qm guest cmd 200 fsfreeze-thaw >&2
frozen=false
zfs clone -o readonly=on "$snapshot" "$clone"
clone_created=true
udevadm settle
loop=$(losetup --find --show --read-only --partscan "/dev/zvol/$clone")
udevadm settle
# Docker's verified root is /dev/sda1; /dev/sda16 is a separate ext4 /boot.
root_partition="${loop}p1"
[[ $(blkid -p -s TYPE -o value "$root_partition") == ext4 ]]
mount -o ro,noload "$root_partition" "$work"
volumes="$work/var/lib/docker/100000.100000/volumes"
if [[ $# == 0 ]]; then
  set -- grafana-loki_grafana_data grafana-loki_loki_data beszel_beszel_data
fi
paths=()
for name in "$@"; do
  [[ $name =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]+$ ]]
  [[ -d "$volumes/$name/_data" && ! -L "$volumes/$name" ]]
  paths+=("$name/_data")
done
extra=()
if [[ -d "$work/opt/paperless-consume" ]]; then
  extra=(-C "$work" opt/paperless-consume)
fi
tar --acls --xattrs --numeric-owner -C "$volumes" -cpf - "${paths[@]}" "${extra[@]}"
