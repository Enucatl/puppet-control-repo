#!/usr/bin/env bash

set -euo pipefail

# Configuration
SOURCE_DIR="/var/lib/docker/100000.100000/volumes/infra_vault_data/_data"
BACKUP_DIR="/scratch/backup/vault"
DATE=$(date --iso-8601=seconds)
ARCHIVE_NAME="vault-backup-${DATE}.tar.gz"
DESTINATION="${BACKUP_DIR}/${ARCHIVE_NAME}"
TAG="vault-backup" # This creates a tag we can search for later

# Helper function to log to Syslog AND Standard Output
log_msg() {
    # -t sets the tag, -s prints to stderr as well as syslog
    logger -t "$TAG" -s "[$TAG]: $1"
}

# Ensure backup directory exists
mkdir -p "$BACKUP_DIR"

log_msg "Starting Vault Backup..."

# 1. Freeze Filesystem
fsfreeze -f "$SOURCE_DIR"

# 2. Create the archive
# We capture any error output from tar into a variable to log it if it fails
TAR_OUTPUT=$(tar -czf "$DESTINATION" "$SOURCE_DIR" 2>&1)
TAR_EXIT=$?

# 3. Unfreeze Filesystem
fsfreeze -u "$SOURCE_DIR"

# 4. Check result
if [ $TAR_EXIT -eq 0 ]; then
  log_msg "SUCCESS: Backup created at ${DESTINATION}"
else
  log_msg "ERROR: Tar command failed. Exit code: ${TAR_EXIT}. Details: ${TAR_OUTPUT}"
  exit 1
fi

# 5. Cleanup
# We don't necessarily need to log the cleanup details unless files are actually deleted
find "$BACKUP_DIR" -name "vault-backup-*.tar.gz" -mtime +15 -delete
log_msg "Cleanup routine finished."
