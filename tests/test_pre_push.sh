#!/bin/bash
# Exercise push rejection, committed snapshots and branch deletion without a remote.
set -euo pipefail
hook=$(realpath "$(dirname "$0")/../.githooks/pre-push")
launcher=$(realpath "$(dirname "$0")/../scripts/check.sh")
fixture=$(mktemp -d)
trap 'rm -rf "$fixture"' EXIT
cd "$fixture"
git init -q
git config user.email test@example.invalid
git config user.name test
mkdir scripts
cat > scripts/check.sh <<'CHECK'
#!/bin/bash
set -euo pipefail
[[ $1 == --archive ]]
mkdir snapshot
tar -xzf - -C snapshot
[[ $(cat snapshot/content) == committed ]]
printf checked > checked
exit 17
CHECK
chmod +x scripts/check.sh
printf committed > content
git add .
git commit -qm fixture
printf uncommitted > content
sha=$(git rev-parse HEAD)
zero=0000000000000000000000000000000000000000
status=0
"$hook" <<< "refs/heads/main $sha refs/heads/main $zero" || status=$?
[[ $status == 17 && -f checked ]]
rm checked
"$hook" <<< "(delete) $zero refs/heads/main $sha"
[[ ! -e checked ]]
echo 'Pre-push rejection, committed snapshot and deletion checks passed'

cp "$launcher" scripts/check.sh
mkdir bin
cat > bin/docker <<'DOCKER'
#!/bin/bash
set -euo pipefail
cat >/dev/null
printf 'called\n' >> docker-calls
exit "${DOCKER_TEST_STATUS:-0}"
DOCKER
chmod +x bin/docker
export PATH="$fixture/bin:$PATH"
scripts/check.sh
[[ $(wc -l < docker-calls) == 1 ]]
status=0
DOCKER_TEST_STATUS=23 scripts/check.sh || status=$?
[[ $status == 23 && $(wc -l < docker-calls) == 2 ]]
echo 'Manual launcher runs once and propagates Docker failures'
