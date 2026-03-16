#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/config.sh"
source "$SCRIPT_DIR/lib.sh"

# Configuration
export USER=root
export HOME=/root
MAC_ADDRESS="30:56:0f:5e:a9:de"
DROPBEAR_HOST="dropbear.proxmox-cortex.${DOMAIN_SUFFIX}"
HOST="proxmox-cortex.${DOMAIN_SUFFIX}"
SHUTDOWN_DELAY_MINS=120

# --- 0. CHECK IF SERVER IS ALREADY ON ---
ALREADY_ON=false
if nc -z -w 2 "$HOST" 22 2>/dev/null; then
    echo "[+] Server is already running. Will not schedule shutdown."
    ALREADY_ON=true
fi

# --- 1. LOAD ENVIRONMENT VARIABLES ---
load_env /var/lib/vz/snippets/.env

: "${VAULT_TOKEN:?VAULT_TOKEN is not set}"
: "${VAULT_ADDR:?VAULT_ADDR is not set}"

if [ "$ALREADY_ON" = true ]; then
    echo "[+] Server was already on — nothing to do."
    exit 0
fi

# 1. Retrieve Password from Vault
echo "[-] Retrieving password from Vault..."
SERVER_PASS=$(vault kv get -field=proxmox-cortex kv/bitwarden)
RET_VAL=$?

# Check if the password variable is empty or if vault failed
if [ $RET_VAL -ne 0 ] || [ -z "$SERVER_PASS" ]; then
    echo "[!] Error: Could not retrieve password from Vault. Exiting."
    exit 1
fi
echo "[+] Password retrieved successfully."

# 2. Send Wake-on-LAN
echo "[-] Sending Wake-on-LAN packet to $MAC_ADDRESS..."
wakeonlan "$MAC_ADDRESS"

# 3. Connect to Dropbear and Unlock
echo "[-] Waiting for Dropbear SSH ($DROPBEAR_HOST) to become available..."
sleep 60

echo "[+] Attempting to unlock via expect..."
SERVER_PASS="$SERVER_PASS" expect <<EOF
  log_user 1
  set timeout 10

  # Spawn the SSH connection
  spawn ssh -p 2222 $DROPBEAR_HOST

  # Handle the prompt.
  expect {
    "password for rpool/ROOT" {
      send "\$env(SERVER_PASS)\r"
    }
    timeout {
      puts "\n[!] Timed out waiting for password prompt."
      exit 1
    }
  }

  # Wait for the process to finish (EOF usually implies the server closed connection after unlock)
  expect eof
EOF

if [ $? -eq 0 ]; then
    echo "[+] Unlock command sent successfully."
else
    echo "[!] Failed to send unlock command."
    exit 1
fi

# 4. Wait for Main OS to boot
echo "[-] Waiting for Main OS ($HOST) to boot..."

# Loop until Main OS SSH is open (timeout 180 seconds)
count=0
while ! nc -z -w 1 "$HOST" 22; do
  sleep 5
  count=$((count+1))
  if [ $count -ge 36 ]; then
      echo "[!] Timed out waiting for Main OS."
      exit 1
  fi
done

echo "[+] Main OS is up."

# 5. Schedule shutdown (only because we woke it up)
echo "[-] Scheduling shutdown in $SHUTDOWN_DELAY_MINS minutes..."
ssh "$HOST" "shutdown +$SHUTDOWN_DELAY_MINS 'Automated shutdown after 2 hours of use'"
