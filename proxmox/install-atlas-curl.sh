#!/bin/bash
set -euo pipefail

packages=${1:?Usage: install-atlas-curl.sh PACKAGE_DIRECTORY}
test "$(id -u)" = 0
. /etc/os-release
test "$ID:$VERSION_ID:$(dpkg --print-architecture)" = 'ubuntu:26.04:amd64'
if test "$(dpkg-query -W -f='${Version}' curl)" = '8.18.0-1ubuntu2.8+atlas1' \
   && test "$(dpkg-query -W -f='${Version}' libcurl4t64)" = '8.18.0-1ubuntu2.8+atlas1' \
   && test -z "$(dpkg --verify curl libcurl4t64)"; then
  exit 0
fi
cd "$packages"
sha256sum --strict --check SHA256SUMS
for package in curl libcurl4t64; do
  artifact="${package}_8.18.0-1ubuntu2.8+atlas1_amd64.deb"
  test "$(dpkg-deb -f "$artifact" Package)" = "$package"
  test "$(dpkg-deb -f "$artifact" Version)" = '8.18.0-1ubuntu2.8+atlas1'
  test "$(dpkg-deb -f "$artifact" Architecture)" = amd64
  case "$(dpkg-query -W -f='${Version}' "$package")" in
    8.18.0-1ubuntu2.8|8.18.0-1ubuntu2.8+atlas1) ;;
    *) echo "Unexpected installed $package version; review the backport." >&2; exit 1 ;;
  esac
done
dpkg -i ./libcurl4t64_8.18.0-1ubuntu2.8+atlas1_amd64.deb \
        ./curl_8.18.0-1ubuntu2.8+atlas1_amd64.deb
