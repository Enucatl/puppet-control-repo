#!/usr/bin/env bash
# Load Grafana's published upstream Docker archive without rebuilding it.
set -euo pipefail
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
gh run download 39395647570 --repo grafana/grafana \
  --name artifacts-docker-linux-amd64 --dir "$work"
mapfile -t archives < <(find "$work" -type f -name '*.docker.tar.gz')
[[ ${#archives[@]} == 1 ]]
archive=${archives[0]}
[[ $(sha256sum "$archive" | cut -d' ' -f1) == $(cat "$archive.sha256") ]]
ssh -o BatchMode=yes user_l@atlas.home.arpa sudo -n docker load < "$archive"
image=$(python3 - "$archive" <<'PY'
import hashlib,json,sys,tarfile
with tarfile.open(sys.argv[1], 'r:*') as archive:
    manifest=json.load(archive.extractfile('manifest.json'))
    assert len(manifest)==1
    config=archive.extractfile(manifest[0]['Config']).read()
    print('sha256:'+hashlib.sha256(config).hexdigest())
PY
)
ssh -o BatchMode=yes user_l@atlas.home.arpa \
  sudo -n docker tag "$image" grafana/grafana:13.3.0-39395647570-official
ssh -o BatchMode=yes user_l@atlas.home.arpa \
  sudo -n docker run --rm --network none --read-only \
  --entrypoint /usr/share/grafana/bin/grafana "$image" --version
printf 'Official upstream Grafana image: %s\n' "$image"
