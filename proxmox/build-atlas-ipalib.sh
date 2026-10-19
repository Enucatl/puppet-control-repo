#!/bin/bash
set -euo pipefail

output=${1:?Usage: build-atlas-ipalib.sh OUTPUT_DIRECTORY}
repo=$(cd "$(dirname "$0")/.." && pwd)
docker build --platform linux/amd64 --progress plain \
  --output "type=local,dest=$output" "$repo/docker/freeipa-client"
artifact="$output/python3-ipalib_4.13.1-1+atlas1_all.deb"
test "$(dpkg-deb -f "$artifact" Package)" = python3-ipalib
test "$(dpkg-deb -f "$artifact" Version)" = '4.13.1-1+atlas1'
test "$(dpkg-deb -f "$artifact" Architecture)" = all
