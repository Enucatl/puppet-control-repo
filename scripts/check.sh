#!/bin/bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

source_dir=$PWD
if [[ ${1:-} == --archive || ( ${1:-} == --image && -n ${2:-} ) ]]; then
    source_dir=$(mktemp -d)
    trap 'rm -rf "$source_dir"' EXIT
    if [[ $1 == --archive ]]; then
        tar -xzf - -C "$source_dir"
    else
        git archive "$2" | tar -xf - -C "$source_dir"
    fi
fi

# Build only dependency inputs; source changes reuse the prepared runtime.
image_inputs=(docker/checks/Dockerfile scripts/check.sh scripts/ci/install-enrollment.sh scripts/ci/install-profiles.sh Puppetfile pyproject.toml uv.lock provisioning/pyproject.toml provisioning/uv.lock provisioning/src)
image_key=$(cd "$source_dir"; find "${image_inputs[@]}" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1)
image="puppet-control-checks:$image_key"
if ! docker image inspect "$image" >/dev/null 2>&1; then
    # Directory-service UIDs may exceed Docker's user namespace mapping.
    tar --owner=0 --group=0 --numeric-owner -C "$source_dir" -czf - "${image_inputs[@]}" |
        docker build --platform linux/amd64 -t "$image" -f docker/checks/Dockerfile - >&2
fi
if [[ ${1:-} == --image ]]; then
    printf '%s\n' "$image"
    exit
fi

run_checks() {
    docker run --rm --userns=host --platform linux/amd64 -i "$image" bash -euc '
        tar -xzf - -C /workspace/puppet-control-repo
        cd /workspace/puppet-control-repo
        scripts/ci/enrollment.sh
        # OpenVox selects its systemd provider by PID 1 name when compiling catalogs.
        printf systemd > /proc/1/comm
        scripts/ci/profiles.sh
        : # Keep this shell as PID 1 so its provider simulation survives.
    '
}

# Tests receive a disposable source copy with no writable host mounts.
if [[ ${1:-} == --archive ]]; then
    tar -C "$source_dir" -czf - . | run_checks
else
    git ls-files --cached --others --exclude-standard -z |
        tar --null --verbatim-files-from --exclude=.env --exclude=secrets \
            --ignore-failed-read -czf - -T - | run_checks
fi
