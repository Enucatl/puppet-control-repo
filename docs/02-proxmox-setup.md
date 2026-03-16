# Load proxmox image through IPMI

# Install with ZFS RAID 1 disk configuration

# Encrypt rpool
https://bitgrounds.tech/posts/proxmox-zfs-encryption/

# Remove enterprise features
[post-pve-install.sh](https://github.com/community-scripts/ProxmoxVE/blob/main/tools/pve/post-pve-install.sh)

# apt update & upgrade

# configure static ips
`/etc/network/interfaces`

```
iface vmbr0 inet static
	address 192.168.2.142
	gateway 192.168.2.1
	bridge-ports enp6s0
	bridge-stp off
	bridge-fd 0

iface vmbr0 inet6 static
	address [prefix]:9e6b:ff:fec3:761d/64
	gateway fe80::6e99:61ff:fe19:1ea7
	bridge-ports enp6s0
	bridge-stp off
	bridge-fd 0

```
