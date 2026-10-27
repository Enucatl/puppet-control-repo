#!/bin/bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

# Tests receive a disposable source copy with no writable host mounts.
if [[ ${1:-} != --archive ]]; then
    git ls-files --cached --others --exclude-standard -z |
        tar --null --verbatim-files-from --exclude=.env --exclude=secrets \
            --ignore-failed-read -czf - -T - | "$0" --archive
    exit
fi
exec docker run --rm --userns=host --platform linux/amd64 -i ubuntu:24.04 bash -euc '
    export DEBIAN_FRONTEND=noninteractive UV_PYTHON=3.14
    mkdir -p /workspace/puppet-control-repo
    tar -xzf - -C /workspace/puppet-control-repo
    apt-get update
    apt-get install -y ca-certificates curl git gnupg sudo unzip systemd docker.io docker-compose-v2
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="/root/.local/bin:/opt/puppetlabs/puppet/bin:$PATH"
    cd /workspace/puppet-control-repo
    git clone https://github.com/Enucatl/docker-compose-security-baseline.git /workspace/compose-security-baseline
    git -C /workspace/compose-security-baseline checkout 41c57257d33d471e2ae7cc7264938944c67434fd
    scripts/ci/enrollment.sh
    # OpenVox selects its systemd provider by PID 1 name when compiling catalogs.
    printf systemd > /proc/1/comm
    scripts/ci/profiles.sh
    : # Keep this shell as PID 1 so its provider simulation survives.
'
