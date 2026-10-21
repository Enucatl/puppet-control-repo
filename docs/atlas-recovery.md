# Atlas backup and recovery

Atlas uses the normal Proxmox Backup Server workflow documented in
[Proxmox backup configuration](04-proxmox-backup-server.md). Restore the Atlas VM
from Proxmox/PBS, retaining its Docker volumes, service secrets, host identities
and Docker subordinate-ID mapping. Keep the original VM offline while starting
a replacement with the same identities. Use the existing Compose definitions
for established services; do not run fresh authority initialization over a
restored installation.

From the restored repository, start the established authorities with:

```sh
docker compose up -d --wait openvox vault vault-unsealer
docker compose --env-file /dev/null -f docker/freeipa/atlas-compose.yaml up -d --wait
```

Resume the native Atlas Puppet agent for its normal certificate renewal and
service refreshes. There is no separate authority startup or renewal script.

Canonical credentials and certificate PEMs live in
`/opt/docker/puppet-control-repo/docker/{vault,freeipa}/secrets/`. They are
excluded from Git and must be present in backups along with Docker's persistent
volumes. See [service configuration](atlas-services.md),
[FreeIPA startup](../docker/freeipa/atlas-compose.yaml) and
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
