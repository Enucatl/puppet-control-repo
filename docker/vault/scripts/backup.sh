#!/usr/bin/env bash

set -euo pipefail
umask 077
unset DOCKER_HOST DOCKER_CONTEXT

[[ $(id -u) == 0 && $(hostname -f) == atlas.home.arpa ]] || {
  echo 'Run as root on atlas.home.arpa.' >&2; exit 1;
}
exec 9>/run/lock/atlas-openvox-maintenance.lock
flock -w 300 9

# Configuration
SOURCE_DIR="/var/lib/docker/100000.100000/volumes/infra_vault_data/_data"
BACKUP_DIR="/scratch/backup/vault"
COMPOSE_DIR="/opt/docker/puppet-control-repo"
COMPOSE=(env -i "PATH=$PATH" DOCKER_DOMAIN=home.arpa docker compose
  --env-file /dev/null -p infra -f "$COMPOSE_DIR/compose.yaml")
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
install -d -o root -g backrest -m 0750 "$BACKUP_DIR"
[[ -d $SOURCE_DIR && ! -e $DESTINATION ]] || {
  echo 'Refusing missing authority state or an existing backup destination.' >&2; exit 1;
}

log_msg "Starting Vault Backup..."

cleanup() {
  local status=$?
  trap - EXIT
  if (( VAULT_STOPPED )); then
    if ! "${COMPOSE[@]}" start vault vault-unsealer; then
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
"${COMPOSE[@]}" stop vault-unsealer vault

tar --acls --xattrs --numeric-owner -czf "$DESTINATION" -C "$SOURCE_DIR" .
tar -tzf "$DESTINATION" >/dev/null
chgrp backrest "$DESTINATION"
chmod 0640 "$DESTINATION"
ARCHIVE_COMPLETE=1

"${COMPOSE[@]}" start vault vault-unsealer
VAULT_STOPPED=0
for attempt in {1..90}; do
  if [[ $(docker inspect --format '{{.State.Health.Status}}' infra-vault-1 infra-vault-unsealer-1) == $'healthy\nhealthy' ]]; then
    log_msg "SUCCESS: Backup created at ${DESTINATION}"
    break
  fi
  if (( attempt == 90 )); then
    log_msg 'ERROR: Vault did not become healthy after backup.'
    exit 1
  fi
  sleep 2
done

# We don't necessarily need to log the cleanup details unless files are actually deleted
find "$BACKUP_DIR" -name "vault-backup-*.tar.gz" -mtime +15 -delete
log_msg "Cleanup routine finished."
