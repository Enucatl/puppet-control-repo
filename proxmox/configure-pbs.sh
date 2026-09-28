#!/bin/bash
# Configure PBS datastore, namespace-scoped credentials, and server-side retention.
# Run as root ON the PBS server. Credential output contains secrets; save securely.
# Usage: configure-pbs.sh -d DATASTORE -p PATH [-n NAMESPACE,...] [-u PREFIX] [-t TOKEN]

set -euo pipefail

DATASTORE_NAME=""
DATASTORE_PATH=""
NAMESPACES="chronicle,proxmox-cortex"
USERNAME_PREFIX="backup"
TOKEN_NAME="backup-token"

usage() {
    echo "Usage: $0 -d DATASTORE_NAME -p DATASTORE_PATH [-n NAMESPACES] [-u PREFIX] [-t TOKEN_NAME]"
    echo ""
    echo "  -d  Datastore name (e.g. backups)"
    echo "  -p  Filesystem path for the datastore (e.g. /mnt/backups)"
    echo "  -n  Comma-separated top-level namespaces (default: chronicle,proxmox-cortex)"
    echo "  -u  Username prefix; accounts are PREFIX-NAMESPACE@pbs (default: backup)"
    echo "  -t  API token name within each account (default: backup-token)"
    echo ""
    echo "PBS keeps the last 3 backups per group, pruning each namespace daily."
    exit 1
}

while getopts "d:p:n:u:t:h" opt; do
    case $opt in
        d) DATASTORE_NAME="$OPTARG" ;;
        p) DATASTORE_PATH="$OPTARG" ;;
        n) NAMESPACES="$OPTARG" ;;
        u) USERNAME_PREFIX="$OPTARG" ;;
        t) TOKEN_NAME="$OPTARG" ;;
        *) usage ;;
    esac
done

: "${DATASTORE_NAME:?-d DATASTORE_NAME is required}"
: "${DATASTORE_PATH:?-p DATASTORE_PATH is required}"

# These names become account IDs, ACL paths, and prune job IDs.
for name in "$DATASTORE_NAME" "$USERNAME_PREFIX" "$TOKEN_NAME"; do
    [[ "$name" =~ ^[A-Za-z0-9_][A-Za-z0-9_-]*$ ]] || {
        echo "Invalid name: $name" >&2; exit 1;
    }
done
[[ "$NAMESPACES" =~ ^[A-Za-z0-9_][A-Za-z0-9_-]*(,[A-Za-z0-9_][A-Za-z0-9_-]*)*$ ]] || {
    echo "Namespaces must be comma-separated top-level names." >&2; exit 1;
}
IFS=',' read -ra NS_LIST <<< "$NAMESPACES"

# PBS's default tables put the identifier in the second whitespace field.
# Consume the whole output to avoid SIGPIPE masking command failures with pipefail.
has_id() {
    awk -v id="$1" '$2 == id { found = 1 } END { exit !found }'
}

echo "=== Proxmox Backup Server Setup ==="
echo "Datastore:  $DATASTORE_NAME -> $DATASTORE_PATH"
echo "Namespaces: $NAMESPACES"

if proxmox-backup-manager datastore list | has_id "$DATASTORE_NAME"; then
    echo "Datastore already exists."
else
    proxmox-backup-manager datastore create "$DATASTORE_NAME" "$DATASTORE_PATH"
fi

# Adopted datastores may have a namespace parent owned by root.
if [ -d "${DATASTORE_PATH}/ns" ]; then
    chown backup:backup "${DATASTORE_PATH}/ns"
fi

FINGERPRINT=$(proxmox-backup-manager cert info \
    | grep -i "Fingerprint (sha256)" | awk '{print $NF}')

for ns in "${NS_LIST[@]}"; do
    PBS_USER="${USERNAME_PREFIX}-${ns}@pbs"
    PBS_TOKEN="${PBS_USER}!${TOKEN_NAME}"
    ACL_PATH="/datastore/${DATASTORE_NAME}/${ns}"
    PRUNE_JOB="prune-${DATASTORE_NAME}-${ns}"

    # Create namespaces with the local administrative API, never the backup token.
    if ! proxmox-backup-debug api get "/admin/datastore/${DATASTORE_NAME}/namespace" \
        | has_id "$ns"; then
        # Some PBS 4 builds panic formatting a successful create response.
        CREATE_STATUS=0
        proxmox-backup-debug api create "/admin/datastore/${DATASTORE_NAME}/namespace" \
            --name "$ns" || CREATE_STATUS=$?
        if ! proxmox-backup-debug api get "/admin/datastore/${DATASTORE_NAME}/namespace" \
            | has_id "$ns"; then
            echo "Failed creating namespace '$ns' (exit $CREATE_STATUS)" >&2
            exit 1
        fi
    fi

    if ! proxmox-backup-manager user list | has_id "$PBS_USER"; then
        # No password: this account authenticates only through its API token.
        proxmox-backup-manager user create "$PBS_USER"
    fi
    TOKEN_VALUE=""
    if proxmox-backup-manager user list-tokens "$PBS_USER" | has_id "$PBS_TOKEN"; then
        echo "Token '$PBS_TOKEN' already exists; keep its previously saved secret."
    else
        TOKEN_VALUE=$(proxmox-backup-manager user generate-token "$PBS_USER" "$TOKEN_NAME" \
            --comment "PVE ${ns} backup agent" \
            | grep -oP '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
    fi

    # Effective token permissions are the intersection of user and token ACLs.
    # DatastoreBackup permits read/write of owned groups, but no prune or delete.
    for auth_id in "$PBS_USER" "$PBS_TOKEN"; do
        proxmox-backup-manager acl update "$ACL_PATH" DatastoreBackup \
            --auth-id "$auth_id" --propagate false
    done

    if proxmox-backup-manager prune-job list | has_id "$PRUNE_JOB"; then
        proxmox-backup-manager prune-job update "$PRUNE_JOB" \
            --store "$DATASTORE_NAME" --ns "$ns" --schedule daily \
            --keep-last 3 --max-depth 0 --disable false \
            --delete keep-hourly --delete keep-daily --delete keep-weekly \
            --delete keep-monthly --delete keep-yearly
    else
        proxmox-backup-manager prune-job create "$PRUNE_JOB" \
            --store "$DATASTORE_NAME" --ns "$ns" --schedule daily \
            --keep-last 3 --max-depth 0
    fi

    echo ""
    echo "=== Credentials for namespace: $ns ==="
    echo "Copy only this namespace's credentials into its cluster's restricted .env:"
    echo "  PBS_SERVER=chronicle.home.arpa"
    echo "  PBS_DATASTORE=$DATASTORE_NAME"
    echo "  PBS_USER=$PBS_USER"
    echo "  PBS_TOKEN_NAME=$TOKEN_NAME"
    if [ -n "$TOKEN_VALUE" ]; then
        echo "  PBS_TOKEN_VALUE=$TOKEN_VALUE"
    else
        echo "  # PBS_TOKEN_VALUE omitted: retain the existing secret; PBS cannot retrieve it."
    fi
    echo "  PBS_FINGERPRINT=$FINGERPRINT"
    echo "Namespace for configure-pve-backups.sh: -n $ns"
done

echo ""
echo "PBS retention configured: last 3 backups per group, daily, without namespace recursion."
echo "Configure a PVE workload only when wanted; creating credentials does not require one."
