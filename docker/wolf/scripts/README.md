# Wolf Bootstrap Scripts

These scripts collect the Wolf-specific changes to the other systems in one
place.

Run them in this order:

1. `10-proxmox-token.py` on the Proxmox host as root
2. `20-vault-wolf.sh` anywhere with Vault CLI access
3. `30-create-wolf-operator.sh` anywhere with access to the FreeIPA container

The Dropbear initramfs key is still generated locally and added through Puppet
data under `data/nodes/proxmox-cortex.yaml`.

Capture the Dropbear host key while the initramfs SSH endpoint is up:

```bash
ssh-keyscan -p 2222 dropbear.proxmox-cortex.home.arpa > docker/wolf/secrets/dropbear_known_hosts
```
