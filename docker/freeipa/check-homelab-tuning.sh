#!/usr/bin/env bash
set -euo pipefail

# Runnable check for the live configuration and resulting process counts.
docker exec -i freeipa bash -s <<'EOF'
set -euo pipefail
grep -Eq '^WSGIDaemonProcess ipa processes=1 threads=1 ' /etc/httpd/conf.d/ipa.conf
grep -Eq '^WSGIDaemonProcess kdcproxy processes=1 ' /etc/httpd/conf.d/ipa-kdc-proxy.conf
[[ $(grep '^KRB5KDC_ARGS=' /etc/sysconfig/krb5kdc | sort -u) == "KRB5KDC_ARGS='-w 2'" ]]
httpd -t
systemctl is-active httpd krb5kdc
systemctl is-active ipa
systemctl show ipa -p ExecStartPost --value | grep -Fq '/opt/freeipa-homelab/tune-homelab.sh --inside-container'
[[ -r /opt/freeipa-homelab/tune-homelab.sh ]]
processes=$(ps -eo args)
[[ $(grep -c '^(wsgi:ipa) ' <<< "$processes") == 1 ]]
[[ $(grep -c '^(wsgi:kdcproxy) ' <<< "$processes") == 1 ]]
# Two workers plus their supervisor.
[[ $(grep -c '^/usr/sbin/krb5kdc ' <<< "$processes") == 3 ]]
echo 'Live homelab tuning checks passed.'
EOF
