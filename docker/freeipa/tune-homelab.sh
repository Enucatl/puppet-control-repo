#!/usr/bin/env bash
set -euo pipefail

# The same script runs on the host manually and inside FreeIPA at startup.
if [[ ${1:-} != --inside-container ]]; then
    exec docker exec -i freeipa bash -s -- --inside-container < "$0"
fi
umask 077
ipa=$(readlink -f /etc/httpd/conf.d/ipa.conf)
proxy=$(readlink -f /etc/httpd/conf.d/ipa-kdc-proxy.conf)
kdc=$(readlink -f /etc/sysconfig/krb5kdc)
mpm=/etc/httpd/conf.d/zz-homelab-mpm.conf
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

grep -Eq '^WSGIDaemonProcess ipa processes=[0-9]+ threads=1 ' "$ipa"
grep -Eq '^WSGIDaemonProcess kdcproxy processes=[0-9]+ ' "$proxy"
grep -Eq "^KRB5KDC_ARGS='-w [0-9]+'$" "$kdc"
sed -E '/^WSGIDaemonProcess ipa /s/processes=[0-9]+/processes=1/' "$ipa" > "$work/ipa"
sed -E '/^WSGIDaemonProcess kdcproxy /s/processes=[0-9]+/processes=1/' "$proxy" > "$work/proxy"
sed -E "s/^KRB5KDC_ARGS='-w [0-9]+'$/KRB5KDC_ARGS='-w 2'/" "$kdc" > "$work/kdc"
cat > "$work/mpm" <<'MPM'
# One idle HTTP process; retain capacity to grow under load.
<IfModule mpm_event_module>
    StartServers          1
    ThreadsPerChild       25
    MinSpareThreads       10
    MaxSpareThreads       35
</IfModule>
MPM

if cmp -s "$ipa" "$work/ipa" && cmp -s "$proxy" "$work/proxy" &&
   cmp -s "$kdc" "$work/kdc" && cmp -s "$mpm" "$work/mpm"; then
    echo 'Homelab tuning already applied.'
    exit 0
fi

backup=$(mktemp -d /data/homelab-tuning-backup.XXXXXX)
cp -p "$ipa" "$backup/ipa.conf"
cp -p "$proxy" "$backup/ipa-kdc-proxy.conf"
cp -p "$kdc" "$backup/krb5kdc"
if [[ -e $mpm ]]; then cp -p "$mpm" "$backup/zz-homelab-mpm.conf"; fi
echo "Original configuration saved in $backup"
rollback() {
    trap - ERR
    cp -p "$backup/ipa.conf" "$ipa"
    cp -p "$backup/ipa-kdc-proxy.conf" "$proxy"
    cp -p "$backup/krb5kdc" "$kdc"
    if [[ -e $backup/zz-homelab-mpm.conf ]]; then
        cp -p "$backup/zz-homelab-mpm.conf" "$mpm"
    else
        rm -f "$mpm"
    fi
    systemctl restart httpd krb5kdc
    echo 'Tuning failed; original configuration restored.' >&2
    exit 1
}
trap rollback ERR
cat "$work/ipa" > "$ipa"
cat "$work/proxy" > "$proxy"
cat "$work/kdc" > "$kdc"
install -m 644 "$work/mpm" "$mpm"
httpd -t
systemctl restart httpd krb5kdc
systemctl is-active httpd krb5kdc
echo 'Applied: one IPA worker, one KDC proxy worker, two KDC workers, one idle HTTP process.'
