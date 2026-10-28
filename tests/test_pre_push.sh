#!/bin/bash
# Check committed snapshots, successful-result caching and failure propagation.
set -euo pipefail
hook=$(realpath "$(dirname "$0")/../.githooks/pre-push")
launcher=$(realpath "$(dirname "$0")/../scripts/check.sh")
repository=$(dirname "$(dirname "$launcher")")
fixture=$(mktemp -d)
trap 'rm -rf "$fixture"' EXIT
cd "$fixture"
git init -q
git config user.email test@example.invalid
git config user.name test
mkdir scripts bin .githooks
cp "$hook" .githooks/pre-push
hook="$fixture/.githooks/pre-push"
cat > scripts/check.sh <<'CHECK'
#!/bin/bash
set -euo pipefail
if [[ $1 == --image ]]; then
    [[ $2 == $(git rev-parse HEAD) ]]
    echo fixture-image
    exit
fi
[[ $1 == --archive ]]
mkdir -p snapshot
tar -xzf - -C snapshot
[[ $(cat snapshot/content) == committed ]]
printf 'checked\n' >> checked
exit "${CHECK_TEST_STATUS:-17}"
CHECK
cat > bin/docker <<'DOCKER'
#!/bin/bash
set -euo pipefail
[[ $1 == image && $2 == inspect ]]
cat image-id
DOCKER
chmod +x scripts/check.sh bin/docker
export PATH="$fixture/bin:$PATH"
printf runtime-1 > image-id
printf committed > content
git add scripts content .githooks
git commit -qm fixture
printf uncommitted > content
sha=$(git rev-parse HEAD)
zero=0000000000000000000000000000000000000000
push="refs/heads/main $sha refs/heads/main $zero"
for attempt in 1 2; do
    status=0
    "$hook" <<< "$push" || status=$?
    [[ $status == 17 && $(wc -l < checked) == "$attempt" ]]
done
export CHECK_TEST_STATUS=0
"$hook" <<< "$push"
[[ $(wc -l < checked) == 3 ]]
"$hook" <<< "$push"
[[ $(wc -l < checked) == 3 ]]
printf runtime-2 > image-id
"$hook" <<< "$push"
[[ $(wc -l < checked) == 4 ]]
echo '# changed launcher' >> scripts/check.sh
"$hook" <<< "$push"
[[ $(wc -l < checked) == 5 ]]
echo '# changed hook' >> "$hook"
"$hook" <<< "$push"
[[ $(wc -l < checked) == 6 ]]
git commit --allow-empty -qm next
sha=$(git rev-parse HEAD)
"$hook" <<< "refs/heads/main $sha refs/heads/main $zero"
[[ $(wc -l < checked) == 7 ]]
"$hook" <<< "(delete) $zero refs/heads/main $sha"
[[ $(wc -l < checked) == 7 ]]
echo 'Pre-push snapshots, failure propagation and cache invalidation passed'

cp "$launcher" scripts/check.sh
tar -C "$repository" -cf - docker/checks/Dockerfile scripts/ci/install-enrollment.sh \
    scripts/ci/install-profiles.sh Puppetfile pyproject.toml uv.lock \
    provisioning/pyproject.toml provisioning/uv.lock provisioning/src | tar -xf -
cat > bin/docker <<'DOCKER'
#!/bin/bash
set -euo pipefail
if [[ $1 == image && $2 == inspect ]]; then exit 0; fi
cat >/dev/null
printf 'called\n' >> docker-calls
exit "${DOCKER_TEST_STATUS:-0}"
DOCKER
chmod +x bin/docker
scripts/check.sh
[[ $(wc -l < docker-calls) == 1 ]]
status=0
DOCKER_TEST_STATUS=23 scripts/check.sh || status=$?
[[ $status == 23 && $(wc -l < docker-calls) == 2 ]]
echo 'Manual launcher runs once and propagates Docker failures'

git add scripts/check.sh docker/checks/Dockerfile scripts/ci/install-*.sh \
    Puppetfile pyproject.toml uv.lock provisioning/pyproject.toml \
    provisioning/uv.lock provisioning/src
git commit -qm runtime
sha=$(git rev-parse HEAD)
image=$(scripts/check.sh --image)
[[ $(scripts/check.sh --image "$sha") == "$image" ]]
printf '\n# changed dependency\n' >> uv.lock
[[ $(scripts/check.sh --image) != "$image" ]]
[[ $(scripts/check.sh --image "$sha") == "$image" ]]
echo 'Runtime cache follows dependencies from the requested snapshot'
