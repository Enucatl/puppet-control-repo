# On the other node pve-desktop.home.arpa

## Prepare the disk space, format to ZFS:

```bash
openssl rand -base64 12 > /keys/backup.key
sgdisk --zap-all /dev/disk/by-id/ata-WDC_WD4003FFBX-68MU3N0_VBGB03LF
wipefs -a !$
lsblk -o NAME,SIZE,PHY-SEC,LOG-SEC !$     # check for 4096 => ashift=12
zpool create -f -o ashift=12 tank !$
zfs create \
 -o encryption=on -o keyformat=passphrase -o keylocation=file:///keys/backup.key \
 -o recordsize=1M \
 -o compression=lz4 \
 -o atime=off \
 -o xattr=sa \
 -o acltype=posix \
 tank/backup
```

## Install Proxmox backup server
From https://pbs.proxmox.com/docs/installation.html#proxmox-backup-no-subscription-repository

```bash
echo "

Types: deb
URIs: http://download.proxmox.com/debian/pbs
Suites: trixie
Components: pbs-no-subscription
Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg" >> /etc/apt/sources.list.d/proxmox.sources

apt update; apt -y upgrade; apt -y autoremove
apt install -y proxmox-backup-server
```

## Configure namespace credentials and retention

Run `proxmox/configure-pbs.sh` as an administrator on PBS (inside the Chronicle
container when using `create-pbs-lxc.sh`):

```bash
./configure-pbs.sh -d backups -p /mnt/backups
```

The script creates the namespaces administratively, then creates a separate
user and API token for each cluster:

| Cluster namespace | API token | ACL path |
| --- | --- | --- |
| `chronicle` | `backup-chronicle@pbs!backup-token` | `/datastore/backups/chronicle` |
| `proxmox-cortex` | `backup-proxmox-cortex@pbs!backup-token` | `/datastore/backups/proxmox-cortex` |

Each user and its token receive only `DatastoreBackup` on their namespace.
Tokens can back up and restore groups they own, but cannot delete snapshots or
administer PBS. Both grants are required because token permissions are limited
by the owning user's permissions. See the
[PBS permission model](https://pbs.proxmox.com/docs/user-management.html).

`-u` changes the username prefix (default `backup`); `-t` changes the token name
within each account (default `backup-token`). Reruns preserve existing tokens.
Their secrets cannot be retrieved again: retain the original credentials rather
than replacing them with a rerun's output.

PBS owns retention: one daily prune job per namespace retains the last **3**
backups per group, with `max-depth=0` so the job covers only that namespace.
PBS's native scheduler handles overdue jobs when Chronicle starts; leave it
awake until pruning completes. See [PBS maintenance](https://pbs.proxmox.com/docs/maintenance.html).

## Add a cluster's storage and backup job

Copy only the matching namespace's labeled credential block into the PVE node's
`proxmox/.env`, protected with `chmod 600`. It contains `PBS_SERVER`,
`PBS_DATASTORE`, `PBS_USER`, `PBS_TOKEN_NAME`, `PBS_TOKEN_VALUE`, and
`PBS_FINGERPRINT`. Keep secrets out of source control and command logs.

For example, the main cluster uses `PBS_USER=backup-chronicle@pbs` and
`PBS_TOKEN_NAME=backup-token`. For a new storage and workload, run:

```bash
./configure-pve-backups.sh -n chronicle -S chronicle
```

The script creates storage and a weekly backup job. It sets
`prune-backups=keep-all=1` on both, so the client does not request pruning.
Configure retention on PBS; the former `-r` option is removed. If configuring
storage in the PVE UI, use the full API token ID as the username, its secret as
the password, the matching namespace, and the PBS certificate fingerprint.
Select **Keep all backups** for both storage and backup-job retention.

Cortex's credentials can remain restricted on PBS until a workload is needed;
creating its identity does not require adding PVE storage or a backup job.
When needed, use its credential block and `-n proxmox-cortex`.

The existing wake/backup orchestration forwards the configured job's retention
setting. Keep its scheduler settings unchanged when migrating existing jobs.

## Migrate existing shared credentials

Perform this as a PBS/PVE administrator while no backup or prune tasks are active.

1. Save root-only copies of PBS access, token, datastore, and prune configuration
   and PVE storage, storage-secret, and backup-job configuration. Record each
   namespace's backup groups, owners, and snapshot counts.
2. Provision both namespace identities and their ACLs. Keep newly issued secrets
   in restricted files. Use the administrative PBS API to transfer Chronicle's
   five existing groups from the shared token to
   `backup-chronicle@pbs!backup-token`; do not rename or recreate snapshots.
3. Update the main cluster's existing `chronicle` storage with its new token ID
   and secret. Set `prune-backups=keep-all=1` on that storage and job
   `backup-9c78eb79-950c`. Preserve storage disablement, job enablement, schedules,
   and the wake/backup orchestrator's settings. The provisioning script creates
   new resources, so do not use it to recreate these existing resources.
4. Replace legacy PBS prune job `s-76f345fb-1048` with the two namespace jobs:
   `keep-last=3`, `max-depth=0`, schedule `daily`. Remove the legacy job before
   allowing scheduled pruning to resume.
5. Confirm the Chronicle token reads existing backup contents and that ownership
   migration left snapshot counts unchanged. Revoke the shared token, remove all
   ACLs for its user and token (including the extra grant at `/datastore`), and
   disable the legacy user. Remove obsolete credentials from managed configuration.
6. Keep Cortex's secret restricted on PBS without creating a new backup workload.
   Verify each token's effective permissions and use disposable backup groups to
   check backup/restore, sibling-namespace denial, and denial of snapshot deletion.
   Confirm the old token fails authentication, test retention with disposable
   snapshots, and observe a successful scheduled prune while Chronicle is awake.

Ownership migration does not prune data. PBS's next scheduled prune applies the
three-backup policy, so compare migration snapshot counts before that run.
