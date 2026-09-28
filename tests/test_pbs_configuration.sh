#!/usr/bin/env bash
# Provisioning regression checks, isolated from live PBS/PVE configuration.
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
export TEST_WORK=$work
mkdir "$work/bin"
export PATH="$work/bin:$PATH"

cat > "$work/bin/proxmox-backup-manager" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$TEST_WORK/manager.log"
case "$1 $2" in
    'datastore list')
        if [ -e "$TEST_WORK/datastore" ]; then echo '│ backups │'; fi ;;
    'datastore create') touch "$TEST_WORK/datastore" ;;
    'user list')
        for file in "$TEST_WORK"/user-*; do
            [ -e "$file" ] || continue
            basename "$file" | sed 's/^user-//'
        done | awk '{print "│ " $0 " │"}' ;;
    'user create') touch "$TEST_WORK/user-$3" ;;
    'user list-tokens')
        if [ -e "$TEST_WORK/token-$3" ]; then
            echo "│ $3!$(cat "$TEST_WORK/token-$3") │"
        fi ;;
    'user generate-token')
        [ ! -e "$TEST_WORK/token-$3" ]
        echo "$4" > "$TEST_WORK/token-$3"
        case "$3" in
            *chronicle@pbs) echo '│ value │ aaaaaaaa-1111-2222-3333-444444444444 │' ;;
            *) echo '│ value │ bbbbbbbb-1111-2222-3333-444444444444 │' ;;
        esac ;;
    'acl update') ;;
    'prune-job list')
        for file in "$TEST_WORK"/job-*; do
            [ -e "$file" ] || continue
            basename "$file" | sed 's/^job-//'
        done | awk '{print "│ " $0 " │"}' ;;
    'prune-job create') [ ! -e "$TEST_WORK/job-$3" ]; touch "$TEST_WORK/job-$3" ;;
    'prune-job update') [ -e "$TEST_WORK/job-$3" ] ;;
    'cert info') echo 'Fingerprint (sha256): aa:bb:cc' ;;
    *) echo "Unexpected manager command: $*" >&2; exit 99 ;;
esac
MOCK
cat > "$work/bin/proxmox-backup-debug" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$TEST_WORK/debug.log"
case "$1 $2" in
    'api get')
        for file in "$TEST_WORK"/namespace-*; do
            [ -e "$file" ] || continue
            basename "$file" | sed 's/^namespace-//'
        done | awk '{print "│ " $0 " │"}' ;;
    'api create')
        while [ "$1" != --name ]; do shift; done
        touch "$TEST_WORK/namespace-$2" ;;
    *) echo "Unexpected debug command: $*" >&2; exit 99 ;;
esac
MOCK
for command in pvesm pvesh; do
    cat > "$work/bin/$command" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
printf '%s %s\n' "$(basename "$0")" "$*" >> "$TEST_WORK/pve.log"
MOCK
done
chmod +x "$work/bin/"*

bash "$repo/proxmox/configure-pbs.sh" -d backups -p "$work/datastore-path" > "$work/fresh"
for ns in chronicle proxmox-cortex; do
    user="backup-$ns@pbs"
    grep -Fq "PBS_USER=$user" "$work/fresh"
    grep -Fq "=== Credentials for namespace: $ns ===" "$work/fresh"
    grep -Fxq "acl update /datastore/backups/$ns DatastoreBackup --auth-id $user --propagate false" "$work/manager.log"
    grep -Fxq "acl update /datastore/backups/$ns DatastoreBackup --auth-id $user!backup-token --propagate false" "$work/manager.log"
    grep -E "^prune-job create .* --ns $ns( |$)" "$work/manager.log" | grep -F -- '--keep-last 3' | grep -F -- '--max-depth 0' | grep -Fq -- '--schedule daily'
done
[ "$(grep 'PBS_TOKEN_VALUE=' "$work/fresh" | sort -u | wc -l)" = 2 ]
[ "$(grep -c '^acl update ' "$work/manager.log")" = 4 ]
! grep -Eq 'DatastoreAdmin|DatastorePowerUser|acl update /datastore/backups ' "$work/manager.log"
[ "$(grep -c '^user generate-token ' "$work/manager.log")" = 2 ]
[ "$(grep -c '^api create ' "$work/debug.log")" = 2 ]
echo 'PASS: distinct namespace credentials, backup-only user/token ACLs, daily PBS retention of three'

: > "$work/manager.log"
: > "$work/debug.log"
bash "$repo/proxmox/configure-pbs.sh" -d backups -p "$work/datastore-path" > "$work/rerun"
! grep -Eq '^user (create|generate-token|delete-token)|^datastore create|^prune-job create' "$work/manager.log"
! grep -q '^api create ' "$work/debug.log"
! grep -q 'PBS_TOKEN_VALUE=' "$work/rerun"
[ "$(grep -c '^prune-job update ' "$work/manager.log")" = 2 ]
for ns in chronicle proxmox-cortex; do
    grep -E "^prune-job update .* --ns $ns( |$)" "$work/manager.log" | grep -F -- '--keep-last 3' | grep -F -- '--max-depth 0' | grep -F -- '--schedule daily' | grep -Fq -- '--delete keep-monthly'
done
echo 'PASS: reruns preserve tokens, enforce PBS retention, and omit unusable secret assignments'

mkdir "$work/custom"
TEST_WORK="$work/custom" bash "$repo/proxmox/configure-pbs.sh" -d backups -p "$work/datastore-path" -n custom -u cluster -t writer > "$work/custom-output"
grep -Fq 'PBS_USER=cluster-custom@pbs' "$work/custom-output"
grep -Fq 'PBS_TOKEN_NAME=writer' "$work/custom-output"
grep -Fq -- '--auth-id cluster-custom@pbs!writer --propagate false' "$work/custom/manager.log"
: > "$work/manager.log"
for ns in 'chronicle/sibling' 'chronicle,,sibling' ',chronicle'; do
    if bash "$repo/proxmox/configure-pbs.sh" -d backups -p "$work/datastore-path" -n "$ns" > "$work/invalid-output" 2>&1; then
        echo "Invalid namespace accepted: $ns" >&2
        exit 1
    fi
done
[ ! -s "$work/manager.log" ]
echo 'PASS: custom username prefix/token name and namespace validation'

cat > "$work/.env" <<'ENV'
PBS_SERVER=pbs.invalid
PBS_DATASTORE=backups
PBS_USER=backup-chronicle@pbs
PBS_TOKEN_NAME=backup-token
PBS_TOKEN_VALUE=test-secret
PBS_FINGERPRINT=aa:bb:cc
ENV
(
    cd "$work"
    bash "$repo/proxmox/configure-pve-backups.sh" -n chronicle -S chronicle -j existing-job > "$work/pve-output"
)
grep '^pvesm add pbs chronicle ' "$work/pve.log" | grep -F -- '--username backup-chronicle@pbs!backup-token' | grep -Fq -- '--prune-backups keep-all=1'
grep '^pvesh create /cluster/backup ' "$work/pve.log" | grep -F -- '--id existing-job' | grep -Fq -- '--prune-backups keep-all=1'
: > "$work/pve.log"
if (cd "$work"; bash "$repo/proxmox/configure-pve-backups.sh" -n chronicle -r keep-last=1 > "$work/removed-option" 2>&1); then
    echo 'Removed -r option unexpectedly succeeded' >&2
    exit 1
fi
[ ! -s "$work/pve.log" ]
echo 'PASS: storage and backup job disable client pruning; obsolete -r is rejected'
