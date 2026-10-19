#!/bin/bash
set -euo pipefail

packages=${1:?Usage: install-atlas-ipalib.sh PACKAGE_DIRECTORY}
test "$(id -u)" = 0
. /etc/os-release
test "$ID:$VERSION_ID" = 'ubuntu:26.04'
installed=$(dpkg-query -W -f='${Version}' python3-ipalib 2>/dev/null || true)
if test "$installed" = '4.13.1-1+atlas1' \
   && test -z "$(dpkg --verify python3-ipalib)"; then
  exit 0
fi
cd "$packages"
sha256sum --strict --check SHA256SUMS
artifact=python3-ipalib_4.13.1-1+atlas1_all.deb
test "$(dpkg-deb -f "$artifact" Package)" = python3-ipalib
test "$(dpkg-deb -f "$artifact" Version)" = '4.13.1-1+atlas1'
test "$(dpkg-deb -f "$artifact" Architecture)" = all
case "$installed" in
  '')
    OPENSSL_CONF=/etc/ssl/openssl.cnf apt-get update
    OPENSSL_CONF=/etc/ssl/openssl.cnf apt-get install -y --no-install-recommends --no-upgrade --no-remove "$PWD/$artifact"
    ;;
  4.13.1-1|4.13.1-1+atlas1) dpkg -i "$artifact" ;;
  *) echo 'Unexpected installed python3-ipalib version; review the backport.' >&2; exit 1 ;;
esac
