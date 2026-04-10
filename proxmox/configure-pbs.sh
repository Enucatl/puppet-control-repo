#!/bin/bash
# Configure Proxmox Backup Server: datastore, namespaces, user, and API token.
# Run this script ON the PBS node (proxmox-cortex).
#
# Usage: configure-pbs.sh -d DATASTORE -p PATH [-n NAMESPACE,...] [-u USER] [-t TOKEN]
#
# After running, copy the output values into .env on each PVE cluster node,
# then run configure-pve-backups.sh on each cluster.

set -euo pipefail

DATASTORE_NAME=""
DATASTORE_PATH=""
NAMESPACES="chronicle,proxmox-cortex"
USERNAME="backup"
TOKEN_NAME="backup-token"

usage() {
    echo "Usage: $0 -d DATASTORE_NAME -p DATASTORE_PATH [-n NAMESPACES] [-u USERNAME] [-t TOKEN_NAME]"
    echo ""
    echo "  -d  Datastore name (e.g. backups)"
    echo "  -p  Filesystem path for the datastore (e.g. /mnt/backups)"
    echo "  -n  Comma-separated namespace names to create (default: chronicle,proxmox-cortex)"
    echo "  -u  PBS username (default: backup)"
    echo "  -t  API token name (default: backup-token)"
    exit 1
}

while getopts "d:p:n:u:t:h" opt; do
    case $opt in
        d) DATASTORE_NAME="$OPTARG" ;;
        p) DATASTORE_PATH="$OPTARG" ;;
        n) NAMESPACES="$OPTARG" ;;
        u) USERNAME="$OPTARG" ;;
        t) TOKEN_NAME="$OPTARG" ;;
        h) usage ;;
        *) usage ;;
    esac
done

: "${DATASTORE_NAME:?-d DATASTORE_NAME is required}"
: "${DATASTORE_PATH:?-p DATASTORE_PATH is required}"

PBS_USER="${USERNAME}@pbs"
PBS_TOKEN="${PBS_USER}!${TOKEN_NAME}"

echo "=== Proxmox Backup Server Setup ==="
echo "Datastore:  $DATASTORE_NAME -> $DATASTORE_PATH"
echo "Namespaces: $NAMESPACES"
echo "User:       $PBS_USER"
echo "Token:      $TOKEN_NAME"
echo ""

# 1/5 Create datastore
echo "[1/5] Creating datastore '$DATASTORE_NAME' at '$DATASTORE_PATH'..."
if proxmox-backup-manager datastore list | grep -q "$DATASTORE_NAME"; then
    echo "  Already exists, skipping."
else
    proxmox-backup-manager datastore create "$DATASTORE_NAME" "$DATASTORE_PATH"
fi

# When adopting an existing datastore, ensure the namespace parent is writable
# by the PBS service user before future namespace changes.
if [ -d "${DATASTORE_PATH}/ns" ]; then
    chown backup:backup "${DATASTORE_PATH}/ns"
fi

# 2/5 Create backup user (password unused; API token is the auth method)
echo "[2/5] Creating user '$PBS_USER'..."
if proxmox-backup-manager user list | grep -q "$PBS_USER"; then
    echo "  Already exists, skipping."
else
    PBS_PASS=$(openssl rand -base64 24)
    proxmox-backup-manager user create "$PBS_USER" --password "$PBS_PASS"
fi

# 3/5 Grant DatastoreAdmin role (required to create namespaces and read backup data)
echo "[3/5] Granting DatastoreAdmin role to '$PBS_USER' on '/datastore/$DATASTORE_NAME'..."
proxmox-backup-manager acl update "/datastore/$DATASTORE_NAME" DatastoreAdmin \
    --auth-id "$PBS_USER"

# 4/5 Create API token
echo "[4/5] Creating API token '$TOKEN_NAME'..."
if proxmox-backup-manager user list-tokens "$PBS_USER" | grep -q "$TOKEN_NAME"; then
    echo "  Token already exists. Delete it first to regenerate:"
    echo "  proxmox-backup-manager user delete-token $PBS_USER $TOKEN_NAME"
    TOKEN_VALUE="<already exists — delete and re-run to regenerate>"
else
    TOKEN_VALUE=$(proxmox-backup-manager user generate-token "$PBS_USER" "$TOKEN_NAME" \
        --comment "PVE cluster backup agent" \
        | grep -oP '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
fi

# Grant the token its own ACL. PBS tokens do not automatically inherit all user ACLs
# in every workflow, and PVE authenticates as this token, not as the user.
echo "Granting DatastoreAdmin role to '$PBS_TOKEN' on '/datastore/$DATASTORE_NAME'..."
proxmox-backup-manager acl update "/datastore/$DATASTORE_NAME" DatastoreAdmin \
    --auth-id "$PBS_TOKEN"

# 5/5 Create namespaces locally on PBS. PVE will not create missing namespaces
# during backup; the backup fails with "namespace not found".
echo "[5/5] Creating namespaces..."
IFS=',' read -ra NS_LIST <<< "$NAMESPACES"
for ns in "${NS_LIST[@]}"; do
    echo "  -> $ns"
    if proxmox-backup-debug api get "/admin/datastore/${DATASTORE_NAME}/namespace" \
        | awk '{print $2}' | grep -Fxq "$ns"; then
        echo "    Already exists, skipping."
    else
        # proxmox-backup-debug can panic formatting the create response on some
        # PBS 4 builds even when creation succeeds, so verify after the call.
        set +e
        proxmox-backup-debug api create "/admin/datastore/${DATASTORE_NAME}/namespace" \
            --name "$ns"
        CREATE_STATUS=$?
        set -e
        if proxmox-backup-debug api get "/admin/datastore/${DATASTORE_NAME}/namespace" \
            | awk '{print $2}' | grep -Fxq "$ns"; then
            echo "    OK"
        else
            echo "    Failed creating namespace '$ns' (exit $CREATE_STATUS)" >&2
            exit 1
        fi
    fi
done

# Get TLS fingerprint (needed when adding PBS storage in PVE without a CA cert)
FINGERPRINT=$(proxmox-backup-manager cert info \
    | grep -i "Fingerprint (sha256)" | awk '{print $NF}')

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Copy the following into .env on each PVE cluster node:"
echo ""
echo "  PBS_SERVER=chronicle.home.arpa"
echo "  PBS_DATASTORE=$DATASTORE_NAME"
echo "  PBS_USER=$PBS_USER"
echo "  PBS_TOKEN_NAME=$TOKEN_NAME"
echo "  PBS_TOKEN_VALUE=$TOKEN_VALUE"
echo "  PBS_FINGERPRINT=$FINGERPRINT"
echo ""
echo "Then run on the proxmox cluster:"
echo "  ./configure-pve-backups.sh -n chronicle -S chronicle"
echo ""
echo "And on the proxmox-cortex cluster:"
echo "  ./configure-pve-backups.sh -n proxmox-cortex"
