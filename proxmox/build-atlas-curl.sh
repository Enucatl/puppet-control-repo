#!/bin/bash
set -euo pipefail

output=${1:?Usage: build-atlas-curl.sh OUTPUT_DIRECTORY}
repo=$(cd "$(dirname "$0")/.." && pwd)
docker build --platform linux/amd64 --progress plain \
  --output "type=local,dest=$output" "$repo/docker/curl"
for package in curl libcurl4t64; do
  artifact="$output/${package}_8.18.0-1ubuntu2.8+atlas1_amd64.deb"
  test "$(dpkg-deb -f "$artifact" Package)" = "$package"
  test "$(dpkg-deb -f "$artifact" Version)" = '8.18.0-1ubuntu2.8+atlas1'
  test "$(dpkg-deb -f "$artifact" Architecture)" = amd64
done

