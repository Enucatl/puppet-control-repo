#!/usr/bin/env bash
set -euo pipefail

code=${1:?code volume root required}
packaged=${2:?packaged image code required}
[[ -d $code/environments/production && ! -e $code/environments/production/.git ]] || exit 0

# An older empty volume may contain Docker's copy of the packaged template.
if [[ -e $code/packaged-environments ]] || ! diff -qr "$packaged" "$code"; then
    echo 'Unmanaged code differs from the image template; preserve/recover it explicitly.' >&2
    exit 1
fi
mv "$code/environments" "$code/packaged-environments"
install -d -o 999 -g 999 -m 0755 "$code/environments"
