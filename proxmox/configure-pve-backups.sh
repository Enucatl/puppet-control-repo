#!/bin/bash
# Add Proxmox Backup Server storage and configure a daily backup job on a PVE cluster.
# Run on any node in the target PVE cluster.
#
# Reads PBS connection details from .env (PBS_SERVER, PBS_DATASTORE, PBS_USER,
# PBS_TOKEN_NAME, PBS_TOKEN_VALUE, PBS_FINGERPRINT).
#
# Usage: configure-pve-backups.sh -n NAMESPACE [-S STORAGE_ID] [-j JOB_ID] [-s SCHEDULE]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=config.sh
source "$SCRIPT_DIR/config.sh"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

NAMESPACE=""
STORAGE_ID=""
JOB_ID=""
SCHEDULE="mon 01:00"
RETENTION="keep-monthly=6"

usage() {
    echo "Usage: $0 -n NAMESPACE [-S STORAGE_ID] [-j JOB_ID] [-s SCHEDULE] [-r RETENTION]"
    echo ""
    echo "  -n  PBS namespace to store this cluster's backups in (e.g. chronicle, proxmox-cortex)"
    echo "  -S  Storage ID to register in PVE (default: pbs-<namespace>)"
    echo "  -j  Backup job ID (default: pbs-<namespace>-weekly)"
    echo "  -s  Backup schedule in systemd timer format (default: mon 01:00)"
    echo "  -r  Retention policy (default: keep-monthly=6)"
    echo ""
    echo "Required .env vars: PBS_SERVER, PBS_DATASTORE, PBS_USER, PBS_TOKEN_NAME,"
    echo "                    PBS_TOKEN_VALUE, PBS_FINGERPRINT"
    exit 1
}

while getopts "n:S:j:s:r:h" opt; do
    case $opt in
        n) NAMESPACE="$OPTARG" ;;
        S) STORAGE_ID="$OPTARG" ;;
        j) JOB_ID="$OPTARG" ;;
        s) SCHEDULE="$OPTARG" ;;
        r) RETENTION="$OPTARG" ;;
        h) usage ;;
        *) usage ;;
    esac
done

: "${NAMESPACE:?-n NAMESPACE is required}"

load_env

: "${PBS_SERVER:?PBS_SERVER is not set in .env}"
: "${PBS_DATASTORE:?PBS_DATASTORE is not set in .env}"
: "${PBS_USER:?PBS_USER is not set in .env}"
: "${PBS_TOKEN_NAME:?PBS_TOKEN_NAME is not set in .env}"
: "${PBS_TOKEN_VALUE:?PBS_TOKEN_VALUE is not set in .env}"
: "${PBS_FINGERPRINT:?PBS_FINGERPRINT is not set in .env}"

STORAGE_ID="${STORAGE_ID:-pbs-${NAMESPACE}}"
JOB_ID="${JOB_ID:-pbs-${NAMESPACE}-weekly}"

echo "=== PVE Backup Configuration ==="
echo "PBS server:   $PBS_SERVER"
echo "Datastore:    $PBS_DATASTORE"
echo "Namespace:    $NAMESPACE"
echo "Storage ID:   $STORAGE_ID"
echo "Backup job:   $JOB_ID  (schedule: $SCHEDULE, retention: $RETENTION)"
echo ""

# 1/2 Register PBS as a storage target in this cluster
echo "[1/2] Adding PBS storage '$STORAGE_ID' to cluster..."
PBS_AUTHID="${PBS_USER}!${PBS_TOKEN_NAME}"
pvesm add pbs "$STORAGE_ID" \
    --server "$PBS_SERVER" \
    --datastore "$PBS_DATASTORE" \
    --namespace "$NAMESPACE" \
    --username "$PBS_AUTHID" \
    --password "$PBS_TOKEN_VALUE" \
    --fingerprint "$PBS_FINGERPRINT"

# 2/2 Create a backup job covering all VMs and containers
echo "[2/2] Creating backup job '$JOB_ID' (schedule: $SCHEDULE)..."
pvesh create /cluster/backup \
    --id "$JOB_ID" \
    --storage "$STORAGE_ID" \
    --schedule "$SCHEDULE" \
    --all 1 \
    --mode snapshot \
    --compress zstd \
    --notes-template "{{guestname}}" \
    --prune-backups "$RETENTION"

echo ""
echo "=== Done ==="
echo "Storage '$STORAGE_ID' added and backup job '$JOB_ID' scheduled ($SCHEDULE)."
echo "Verify in the PVE web UI under Datacenter -> Storage and Datacenter -> Backup."
