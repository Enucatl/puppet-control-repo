# Atlas backup and recovery

Atlas uses the normal Proxmox Backup Server workflow documented in
[Proxmox backup configuration](04-proxmox-backup-server.md). Restore the Atlas VM
from Proxmox/PBS, retaining its Docker volumes, service secrets, host identities
and Docker subordinate-ID mapping. Keep the original VM offline while starting
a replacement with the same identities. Use the existing Compose definitions
for established services; do not run fresh authority initialization over a
restored installation.

For a FreeIPA backup from before the AlmaLinux to CentOS Stream transition,
follow the [one-time transition procedure](atlas-freeipa.md#one-time-almalinux-to-centos-stream-transition)
before ordinary startup with the current image. Already migrated FreeIPA backups
use ordinary startup.

From the restored repository, start the established authorities with:

```sh
docker compose up -d --wait openvox vault vault-unsealer ipa
```

Resume the native Atlas Puppet agent for its normal certificate renewal and
service refreshes. There is no separate authority startup or renewal script.

Canonical credentials and certificate PEMs live in
`/opt/docker/puppet-control-repo/docker/{vault,freeipa}/secrets/`. They are
excluded from Git and must be present in backups along with Docker's persistent
volumes. See [service configuration](atlas-services.md),
[FreeIPA startup](../docker/docker-compose.yml) and
[authority configuration](openvox-infra.md).

The existing weekly `vault-snapshot.timer` remains enabled: Monday at 01:00 UTC.
It runs `docker/vault/scripts/backup.sh`, briefly stops Vault and its unsealer,
exports consistent file storage to `/scratch/backup/vault`, then restarts and
checks both services. Exports remain readable by Backrest; its existing backup
of that location is unchanged. The export covers Vault storage, so retain the
service `secrets/` and other Docker volumes through the normal backup workflow.

The custom full-authority archive/restore scripts and disposable migration clone
tools are retired. No new Backrest plan or replacement backup system was added.
Verify the existing Proxmox/PBS job covers Atlas's disks and retained secrets,
and validate service recovery before relying on it for the current deployment.

Previously created protected backup kits and acceptance records remain historical
recovery material; retiring tooling does not delete those archives. Historical
restore transcripts are evidence of their original layouts, not current startup
instructions.

## Retained recovery material and limits

Repository cleanup does not authorize deleting private backup kits, VM disks,
held snapshots, staging copies or protected archives. Inventory those assets
separately before changing retention. Complex VM 200, Forbearance VM 300 and
their recovery material remain protected; Cognition's retirement does not
change their retention. See [guest recovery constraints](../proxmox/README.md).

The protected lab kit at
`root@proxmox.home.arpa:/root/openvox-issue12-rehearsal/final-baseline-24001`
contains sensitive test recovery inputs (directory 0700, files 0600); do not
publish it. Earlier revisions 1–3 restore a retired strict-CRL configuration,
not the final baseline. Historical standalone restore needs exact images,
verified configuration/volume/recovery archives and authenticated SSH access
on a fresh Docker/Compose host. It proves lab recovery, not production backup
custody, FreeIPA/NFS state recovery or independent physical storage.

Preserve Atlas's pre-13.3 Grafana data archive: schema migration can prevent an
image-only downgrade. Retain private Aperture records and seed media described
in the provisioning runbook. Cerebrum's missing backup job remains a protection
gap; local rollback assets are not independent backups.

The November 1 live inspection found `root@docker` in Cortex's retained initramfs
images for `6.17.13-21-pve`, `6.17.2-1-pve` and `7.0.14-23-pve`, despite clean
live authorization files. Read-only inspection of both EFI partitions
(`7A10-831F` and `E1F1-1158`) also found that key in their retained
`6.17.13-21-pve` and `7.0.14-23-pve` initramfs copies; current `7.0.14-25-pve`
images were clean. Keep the Puppet key-removal marker until boot-image
revocation is complete. `update-initramfs -u` without `-k all` can leave revoked
Dropbear keys in older images; verify every retained initramfs and EFI copy.
The October inventory did not establish actual key usage; key comments alone
do not prove private-key ownership.
