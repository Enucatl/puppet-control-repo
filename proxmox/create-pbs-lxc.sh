#!/bin/bash
# Create a privileged LXC container and install Proxmox Backup Server inside it.
# Bind-mounts a host ZFS dataset directly into the container for the PBS datastore.
# Run this script on the proxmox-cortex PVE node.
#
# Usage: create-pbs-lxc.sh [-i VMID] [-c CORES] [-m MEMORY_MB] [-p HOST_PATH]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=config.sh
source "$SCRIPT_DIR/config.sh"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

VMID=""
CT_NAME_ARG=""
CORES=2
MEMORY=2048
DISK="8"           # root disk in GB (OS only; backup data lives on the bind mount)
BRIDGE="vmbr0"
HOST_MOUNT="/tank/backups"
CT_MOUNT="/mnt/backups"
STORAGE="$DEFAULT_STORAGE"
TEMPLATE_STORAGE="local"

usage() {
    echo "Usage: $0 [-i VMID] [-n NAME] [-c CORES] [-m MEMORY_MB] [-p HOST_PATH]"
    echo ""
    echo "  -i  Container ID (default: next available)"
    echo "  -n  Hostname for the container (default: random word starting with 'c')"
    echo "  -c  CPU cores (default: 2)"
    echo "  -m  Memory in MB (default: 2048)"
    echo "  -p  Host ZFS dataset path to bind mount (default: /tank/backups)"
    exit 1
}

while getopts "i:n:c:m:p:h" opt; do
    case $opt in
        i) VMID="$OPTARG" ;;
        n) CT_NAME_ARG="$OPTARG" ;;
        c) CORES="$OPTARG" ;;
        m) MEMORY="$OPTARG" ;;
        p) HOST_MOUNT="$OPTARG" ;;
        h) usage ;;
        *) usage ;;
    esac
done

if [ -z "$VMID" ]; then
    VMID=$(pvesh get /cluster/nextid)
fi

CT_NAME="${CT_NAME_ARG:-chronicle}"

echo "--- Creating PBS LXC: CT $VMID ($CT_NAME) ---"
echo "Cores: $CORES  Memory: ${MEMORY}MB  Root disk: ${DISK}GB"
echo "Bind mount: $HOST_MOUNT -> $CT_MOUNT"
echo ""

# 1/5 Download Debian 13 template if not already present
echo "[1/5] Checking for Debian 13 template..."
TEMPLATE=$(pveam available --section system 2>/dev/null \
    | awk '/debian-13-standard/ {print $2}' | sort -V | tail -1)

if [ -z "$TEMPLATE" ]; then
    echo "Error: Could not find debian-13-standard template in pveam."
    exit 1
fi

if ! pveam list "$TEMPLATE_STORAGE" 2>/dev/null | grep -q "$TEMPLATE"; then
    echo "  Downloading $TEMPLATE..."
    pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"
else
    echo "  Found: $TEMPLATE"
fi

# 2/5 Create privileged container
echo "[2/5] Creating container $VMID..."
pct create "$VMID" "${TEMPLATE_STORAGE}:vztmpl/${TEMPLATE}" \
    --hostname "$CT_NAME" \
    --unprivileged 0 \
    --features nesting=1 \
    --cores "$CORES" \
    --memory "$MEMORY" \
    --swap 512 \
    --storage "$STORAGE" \
    --rootfs "${STORAGE}:${DISK}" \
    --net0 "name=eth0,bridge=${BRIDGE},ip=dhcp,ip6=auto" \
    --onboot 1 \
    --ostype debian \
    --ssh-public-keys ~/.ssh/id_ed25519.pub \
    --start 0

cleanup() {
    echo "Error detected. Cleaning up CT $VMID..."
    pct stop "$VMID" 2>/dev/null || true
    pct destroy "$VMID" 2>/dev/null || true
}
trap cleanup ERR INT TERM

# 3/5 Add bind mount for the ZFS dataset
echo "[3/5] Adding bind mount $HOST_MOUNT -> $CT_MOUNT..."
pct set "$VMID" --mp0 "${HOST_MOUNT},mp=${CT_MOUNT}"

# 4/5 Start container
echo "[4/5] Starting container..."
pct start "$VMID"
echo "  Waiting for container to boot..."
sleep 5

# 5/5 Install Proxmox Backup Server
echo "[5/5] Installing Proxmox Backup Server..."
pct exec "$VMID" -- bash -c "
set -e

# Pre-create network interfaces file so ifupdown2 post-install does not hang in LXC
printf 'auto lo\niface lo inet loopback\n' > /etc/network/interfaces

# Proxmox Backup Server's generated network config can still launch dhclient
# for IPv6. Keep that non-blocking so PBS services are not held behind
# network-online.target when DHCPv6 is unavailable and SLAAC/RA is enough.
install -d /etc/network/ifupdown2/policy.d
cat > /etc/network/ifupdown2/policy.d/dhcp-nowait.json <<'EOF'
{
  \"dhcp\": {
    \"defaults\": {
      \"dhcp-wait\": \"no\"
    }
  }
}
EOF

echo '-- Adding Proxmox BS repository...'
wget -q https://enterprise.proxmox.com/debian/proxmox-archive-keyring-trixie.gpg \
    -O /usr/share/keyrings/proxmox-archive-keyring.gpg

cat > /etc/apt/sources.list.d/proxmox.sources <<'EOF'
Types: deb
URIs: http://download.proxmox.com/debian/pbs
Suites: trixie
Components: pbs-no-subscription
Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg
EOF

echo '-- Installing packages...'
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y ifupdown2 proxmox-backup-server jq

# PBS enables the enterprise repository by default. Disable it here so
# later apt operations keep working on hosts using the no-subscription repo.
if [ -f /etc/apt/sources.list.d/pbs-enterprise.sources ]; then
    mv /etc/apt/sources.list.d/pbs-enterprise.sources \
       /etc/apt/sources.list.d/pbs-enterprise.sources.disabled
fi

echo '-- Setting ownership of backup mount...'
chown backup:backup ${CT_MOUNT}

echo '-- Enabling PBS service...'
systemctl enable --now proxmox-backup
"

# Install CA certificates from host
echo "Installing CA certificates..."
CA_DIR="/usr/local/share/ca-certificates/ipa-ca"
if [ -d "$CA_DIR" ]; then
    tar -C /usr/local/share/ca-certificates -cf - ipa-ca \
        | pct exec "$VMID" -- tar -C /usr/local/share/ca-certificates -xf -
    pct exec "$VMID" -- update-ca-certificates
else
    echo "  Warning: $CA_DIR not found on host, skipping."
fi

trap - ERR INT TERM

echo ""
echo "--- Done! ---"
echo "PBS is running in CT $VMID."
echo ""
echo "Access the PBS web UI at: https://${CT_NAME}.${DOMAIN_SUFFIX}:8007"
echo ""
echo "Next steps:"
echo ""
echo "1. Set up the datastore, user, and API token inside the container:"
echo "   pct push $VMID configure-pbs.sh /root/configure-pbs.sh --perms 0755"
echo "   pct exec $VMID -- /root/configure-pbs.sh -d backups -p ${CT_MOUNT}"
echo ""
echo "2. Copy the output values into .env on each PVE cluster node, then run:"
echo "   ./configure-pve-backups.sh -n chronicle -S chronicle # on the proxmox cluster"
echo "   ./configure-pve-backups.sh -n proxmox-cortex # on the proxmox-cortex cluster"
echo ""
echo "Note: create namespaces in PBS first. PVE backups fail if the namespace is missing."
