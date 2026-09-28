#!/usr/bin/env bash

set -euo pipefail

# Configuration
SOURCE_DIR="/var/lib/docker/100000.100000/volumes/infra_vault_data/_data"
BACKUP_DIR="/scratch/backup/vault"
COMPOSE_DIR="/opt/docker/puppet-control-repo/docker"
DATE=$(date --iso-8601=seconds)
ARCHIVE_NAME="vault-backup-${DATE}.tar.gz"
DESTINATION="${BACKUP_DIR}/${ARCHIVE_NAME}"
TAG="vault-backup" # This creates a tag we can search for later
VAULT_STOPPED=0
ARCHIVE_COMPLETE=0

# Helper function to log to Syslog AND Standard Output
log_msg() {
    # -t sets the tag, -s prints to stderr as well as syslog
    logger -t "$TAG" -s "[$TAG]: $1"
}

# Ensure backup directory exists
mkdir -p "$BACKUP_DIR"

log_msg "Starting Vault Backup..."

cleanup() {
  local status=$?
  trap - EXIT
  if (( VAULT_STOPPED )); then
    if ! (cd "$COMPOSE_DIR" && docker compose start vault); then
      log_msg "ERROR: Failed to restart Vault after backup."
      status=1
    fi
  fi
  if (( ! ARCHIVE_COMPLETE )); then
    rm -f "$DESTINATION"
  fi
  exit "$status"
}
trap cleanup EXIT

# Stop Vault so its file storage stays consistent throughout the archive.
VAULT_STOPPED=1
(cd "$COMPOSE_DIR" && docker compose stop vault)

tar -czf "$DESTINATION" "$SOURCE_DIR"
ARCHIVE_COMPLETE=1

(cd "$COMPOSE_DIR" && docker compose start vault)
VAULT_STOPPED=0
log_msg "SUCCESS: Backup created at ${DESTINATION}"

# We don't necessarily need to log the cleanup details unless files are actually deleted
find "$BACKUP_DIR" -name "vault-backup-*.tar.gz" -mtime +15 -delete
log_msg "Cleanup routine finished."
