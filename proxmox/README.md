# Proxmox VM provisioning

Atlas, Aperture and Cerebrum use Ubuntu 26.04; Cerebrum VM 230 replaced
Cognition VM 220 through the [completed migration](../docs/archive/migration/cerebrum-migration.md).
This runbook covers provisioning, bootstrap and publication. Recover established Atlas state
through the [backup and recovery workflow](../docs/atlas-recovery.md).
Completed plans and acceptance results are in the
[migration archive](../docs/archive/migration/README.md).

## Prerequisites and inputs

Run from this repository on the target Proxmox host **as root**. The
[host setup notes](../docs/02-proxmox-setup.md) describe the existing baseline.
Provisioning needs:

- Proxmox `qm` and `pvesh`, bridge `vmbr0` with DHCP, working DNS and outbound
  HTTPS/package access from the host and guests.
- Image storage `local-zfs`; snippet storage `snippets` with content type
  `snippets` and path `/var/lib/vz`; directories `/var/lib/vz/snippets` and
  `/var/lib/vz/template/iso`. On the current host, storage `local` does **not**
  enable snippets.
- Host commands `curl`, `jq`, `envsubst` (gettext), `sha256sum`, `ip`, Git and
  standard shell utilities; root's `~/.ssh/id_ed25519.pub` for guest SSH access.
- Available RAM and system-disk space. Atlas defaults to eight CPU cores,
  8192 MiB RAM and a 300 GiB system disk; its existing scratch data belongs
  in the recovery plan when restoring the established host.
- For desktop provisioning, `uv` with Python 3.14 or newer and access to its
  frozen dependency lock. Random names additionally need `/usr/share/dict/words`.

The template and Atlas base work without `.env`, Vault credentials, an OpenVox
server or an enrollment token. Optional `.env` assignments in the **current
working directory** override [config.sh](config.sh). Base defaults are:

```dotenv
LOG_DIR=/var/log/openvox-provisioning
SNIPPET_STORAGE=snippets
TEMPLATE_ID=9001
```

The [.env example](../.env.example) also documents enrollment inputs. Shell
entry points require simple literal `KEY=value` assignments without whitespace,
quoting, inline comments or shell expansion. Desktop uses literal assignments
and supports quoted values. Shared defaults retain `PUPPET_SERVER=docker.home.arpa`;
select `openvox.home.arpa` explicitly for new enrollment.

## Template and Atlas base

From the repository root on `proxmox.home.arpa`:

```bash
bash proxmox/ubuntu-server-template.sh
bash proxmox/atlas-server.sh
```

The builder defaults to template 9001, named `ubuntu-server-2604-template`.
`-i TEMPLATE_ID` selects a different free ID. It downloads the dated
[Ubuntu 26.04 image](https://cloud-images.ubuntu.com/releases/resolute/release-20260927/ubuntu-26.04-server-cloudimg-amd64.img)
and verifies the same pinned SHA256 on both new downloads and cached files:

```text
8800651811af9a85465ad1d552add729947bb16488dddb4a9b5305a3d97332b2
```

The checksum is published in Canonical's
[SHA256SUMS](https://cloud-images.ubuntu.com/releases/resolute/release-20260927/SHA256SUMS).
The template installs QEMU guest agent and the signed, accepted
`9.1.1+homelab.28001-1+ubuntu26.04` OpenVox agent through
[install-openvox.sh](install-openvox.sh). The same installer provisions applicable
native curl and IPA library fixes from the signed companion APT repository;
[openvox-packages](https://github.com/Enucatl/openvox-packages/blob/main/docs/native-client.md) owns their builds
and release instructions. VM, host and Atlas enrollment use this shared path,
without manually copying `.deb` artifacts. Puppet installs and enrolls the
FreeIPA client later when its catalog applies. The template leaves Puppet
disabled and removes cloud-init instance state, machine identity and SSH host
keys before conversion.
It contains no enrolled host identity or reusable enrollment credential.
Package versions and image/control-repository provenance are written under
`LOG_DIR`; package updates still depend on the upstream repositories.

`atlas-server.sh` selects the unenrolled infrastructure-base mode of
`docker-server.sh`. It creates the short hostname `atlas` and FQDN
`atlas.home.arpa`, persists both in cloud-init configuration, and verifies:

```text
hostname      -> atlas
hostname -f   -> atlas.home.arpa
```

Cloud-init manages the canonical FQDN and short alias in `/etc/hosts` on every
boot; no post-install hostname edits are required. Login uses `user_l` with
root's public key and passwordless sudo. Proxmox receives `--searchdomain home.arpa`
so its generated user-data agrees with the snippet's FQDN. The scripts wait for
cloud-init through the QEMU agent, verify names and append guest output to
`LOG_DIR/cloud-init-output.log`. The shared shell wait expires after 30 minutes.

A fresh base creates `/scratch` on its system disk. Scripted ownership and
tmpfiles cleanup cover `/scratch/apt-cacher-ng` (100101:100101, 0755, two days)
and `/scratch/backup/vault` (root only, 0700, seven days). Local disposable
archives do not replace independently protected recovery backups. Restoring
Atlas must retain its established scratch data and authority state.
The base creates no Vault recovery material; bootstrap and fresh authority
initialization are separate steps below.

Atlas creation uses MAC `bc:24:11:4e:a4:9e`, matching the router DHCP reservation
for `10.0.0.158`. The shared creation script's optional `-a MAC_ADDRESS` replaces
only the clone NIC's MAC, retaining its other network options. An explicit
override must also update the router reservation. This keeps Atlas and its
`vault.home.arpa` / `openvox.home.arpa` service names stable across rebuilds.

## Separate XFS template

Run `bash proxmox/ubuntu-xfs-template.sh -i 9002 -b 9012` from the
reviewed checkout on the Proxmox host. Both IDs must be unused; existing
Ubuntu/ext4 templates remain intact. The disposable builder uses 2048 MiB RAM,
two cores, a 10 GiB cloud-image boot disk and a separate thin 1,000 GiB target. Before booting the builder, the script
checks both ZFS `reservation` and `refreservation` are zero. The virtual capacity
is not allocated up front; physical pool headroom is still required.
Check physical pool headroom and host RAM before running. Use the repository's
`wake-run` workflow when launching the build from a controlling Codex session.

The builder boots the existing pinned Ubuntu 26.04 `release-20260927` cloud
image under UEFI. The builder identifies only the blank target disk by its explicit
`openvox-xfs-target` hardware serial, verifies its size and absence of partitions/mounts,
and passes the validated device path to Curtin (Proxmox udev IDs use a different
`drive-scsi1` identifier). Curtin creates GPT with a 512 MiB FAT32 EFI partition, 1 GiB ext4 `/boot`, and
a final-size XFS root with `reflink=1,ftype=1`. `/home`, `/var`, Docker and Steam remain on
root. The pinned Curtin does not implement remaining-space `size: -1` semantics, so
root uses the supported explicit size `1022462M`: 1,000 GiB minus the EFI and
boot partitions and 2 MiB for GPT/alignment. It is formatted at its final size,
using XFS defaults for allocation groups and journal, without intermediate
filesystem expansion or manual geometry settings. The build checks four
allocation groups and records complete `xfs_info` journal geometry.
Native Curtin hooks install the kernel, initramfs and GRUB; a GRUB debconf
setting requests the removable EFI fallback path for fresh firmware NVRAM.
A GRUB drop-in adds `net.ifnames=0` before early networking starts, preserving
the repository's `eth0` convention without a cloud-init rename race.

Ubuntu 26.04 does not publish Curtin APT packages. The builder uses Canonical's
[Curtin source](https://github.com/canonical/curtin/tree/93c36fb6a0271fefd2bf311418b95eb48f856d5f)
at commit `93c36fb6a0271fefd2bf311418b95eb48f856d5f`, verifies archive SHA256
`a98344999b7aab9b51a6d68f6030a857e99b97a4f61b3e354e626d60d3bd92c9`, and
runs the upstream entry point directly with APT-managed Python/system dependencies.
The source and helpers remain only on the disposable builder; provenance records
the revision and checksum.

The matching
[root-filesystem archive](https://cloud-images.ubuntu.com/releases/resolute/release-20260927/ubuntu-26.04-server-cloudimg-amd64-root.tar.xz)
has its own pinned SHA256:

```text
ccd08a7a76cdc69034dd2682b4913bdd051cb71ad87d0b66689015aa068e61c8
```

This checksum was authenticated independently using Canonical's
[`SHA256SUMS.gpg`](https://cloud-images.ubuntu.com/releases/resolute/release-20260927/SHA256SUMS.gpg)
and the Ubuntu cloud-image keyring. To repeat authentication on an Ubuntu
administrative host, download `SHA256SUMS` and `SHA256SUMS.gpg` from that same
release directory and run:

```bash
gpgv --status-fd 1 --keyring /usr/share/keyrings/ubuntu-cloudimage-keyring.gpg SHA256SUMS.gpg SHA256SUMS
```

Require `VALIDSIG D2EB44626FDDC30B513D5BB71A5D6C4C7DB87C81`, then compare the
archive's specific manifest entry and checksum. The build verifies the pinned
archive checksum before Curtin extraction; the cloud disk has its separate
existing checksum. Preserve authenticated manifests with the migration record.

Only the installed target disk moves to the new template VM. Its first boot
uses a fresh EFI disk and the existing Ubuntu software preparation, including
QEMU guest agent and the shared OpenVox installer. The build verifies XFS,
UEFI, partitions, packages and reboot, then clears machine identity, SSH keys
and cloud-init state. It records Curtin logs, checks, source hashes and image
provenance under `/var/log/openvox-provisioning`. Failed builds retain VMs,
disks and snippets for diagnosis; reruns require choosing unused IDs or
manually reviewing/removing only these owned build resources. Successful builds
retain the stopped builder for review, with no builder disk attached to the
template.

This template requires clone disks **at least 1000G**; the generic provisioning
default of 50G is too small. Before production, clone with
`docker-server.sh -b -t 9002 -d 1000G` using a fresh identity. Verify unique machine/SSH identities, cloud-init,
root expansion, reboot, guest agent, Docker OverlayFS, and disposable reflink,
deduplication and independent writes. The template build alone does not claim
these clone/application checks. Enrolled production clones use the same `-t`
option without `-b` and require the usual Vault/OpenVox inputs.

For later growth, first check physical storage headroom, enlarge the virtual
disk with `qm resize VMID scsi0 NEW_ABSOLUTE_SIZE`, then run `growpart /dev/sda 3`
and `xfs_growfs /` inside the guest after confirming the actual root disk and
partition with `findmnt`/`lsblk`. Root must remain the last partition. XFS can
grow online; it cannot shrink. Cloud-init normally performs partition/filesystem
expansion on a new enlarged clone; verify the result using `lsblk`, `xfs_info /`
and `df -h /`.

## Independent Atlas tooling bootstrap

After base creation, run [atlas-bootstrap.sh](atlas-bootstrap.sh) on Atlas as
root, with the full published control-repository commit SHA as its only argument.
It needs outbound Ubuntu/Docker APT and GitHub access, authenticated Atlas SSH
trust, and an administrator's GitHub SSH credentials. From this repository on
the administrator's machine:

```bash
CONTROL_REVISION=$(git rev-parse HEAD)
ssh -A user_l@atlas.home.arpa \
  "sudo --preserve-env=SSH_AUTH_SOCK bash -s -- '$CONTROL_REVISION'" \
  < proxmox/atlas-bootstrap.sh
```

The SSH agent is forwarded for this invocation; private keys are not copied to
the guest. Repository origins use SSH, and GitHub's published Ed25519 host key
is pinned in the script. The script does not load `.env` or require Vault,
FreeIPA, Puppet enrollment or a live configuration server.

The bootstrap installs Git, curl, GPG, jq and OpenSSH, plus pinned versions of
Docker Engine/CLI, containerd, Compose and Buildx from
[Docker's signed Ubuntu repository](https://docs.docker.com/engine/install/ubuntu/).
It checks out the declared control revision at `/opt/docker/puppet-control-repo`
and the CI-pinned hardening revision at sibling
`/opt/docker/compose-security-baseline`, matching the existing Compose paths.
`/opt/docker` and both checkout roots are owned by `user_l:docker`, mode `2770`:
the group can write, new directories inherit the Docker group, and other users
cannot list or traverse these directories to read deployment secrets. The script
enforces these directory modes on every run without recursively changing secret
file permissions or container data. Both checkouts belong to `user_l:docker`;
Git commands run as `user_l`, using the forwarded SSH agent. The script adds
`user_l` to the Docker group, so a subsequent login can use the Docker socket.
Reruns refuse modified/unrelated checkouts and incompatible Docker settings.
After promotion, bootstrap refuses to run before changing the host or checkouts.
Use publishing for subsequent code updates and recovery procedures for established
state.
The exact earlier minimal bootstrap configuration can be upgraded when Docker
has no containers, volumes or custom networks. The script stops the daemon and
socket before changing its settings and mapping, then restarts it after installation.

Docker retains `ipv6=true`, `userns-remap=default`, `userland-proxy=false` and
bounded JSON logs (`10m`, three files), plus the existing experimental flag.
The template initially assigns `user_l:100000:65536` in both subordinate-ID files.
The bootstrap transfers this allocation to Docker's dedicated `dockremap`
account before Docker installation and refuses conflicts with another account.
This preserves `100000:100000` container ownership without overlapping ranges.
Recovery must retain the `dockremap` account and its numeric mapping.

Atlas uses these Docker network ranges:

| Setting | Atlas value |
| --- | --- |
| IPv4 default pool | `172.17.0.0/12`, allocated as `/20` networks |
| IPv4 default bridge (`bip`) | `172.20.0.1/16` |
| IPv6 default pool | `2a02:168:6278:a400::/56`, allocated as `/64` networks |
| IPv6 default bridge (`fixed-cidr-v6`) | `2a02:168:6278:a500::/64` |

`bip` specifies the `docker0` bridge's IPv4 gateway address and subnet; it is
separate from pools used for automatically created Compose networks. The IPv4
settings match the old Docker host. Standard bridge networking uses NAT and
published host ports, so these host-local ranges can be reused on both hosts.
`172.17.0.0/12` covers `172.16.0.0/12`; Docker reserves the bridge subnet when
allocating networks from that pool. The IPv6 pool and bridge are separate from
each other and the existing hosts' IPv6 ranges.
The proposed `a200::/56` and `a300::/64` ranges are already assigned to the desktop
in [the router template](../provisioning/templates/partials/nat_routing.j2).
The router template routes `a400::/56` and `a500::/64` through Atlas's link-local
address. Apply router provisioning before enabling routed IPv6 workloads;
this bootstrap does not apply router changes.

The bootstrap installs
[Sysbox CE 0.7.1](https://github.com/nestybox/sysbox/releases/tag/v0.7.1) from
`sysbox-ce_0.7.1.linux_amd64.deb`, verifies the published SHA256
`9d6d5484f980d0a17f86c492c1262015c2afb66280bdb97215b79fde6a0261c5`,
and registers `/usr/bin/sysbox-runc` as an optional Docker runtime. APT installs
its dependencies; Sysbox is installed before Docker so the runtime binary exists
when the daemon first starts. The script checks the package version
`0.7.1.linux`, the three Sysbox services and Docker's runtime registration.
Ubuntu's enforced `fusermount3` AppArmor profile also needs permission to mount
and unmount under `/var/lib/sysboxfs`. The script preserves existing local rules,
adds the [upstream-recommended permissions](https://github.com/nestybox/sysbox/issues/947#issuecomment-3071416102)
to `/etc/apparmor.d/local/fusermount3` and reloads the profile. AppArmor remains
enforced; reruns do not duplicate the rules.

The script starts Docker, verifies its API and namespace mapping, and checks
Compose/Buildx and a sanitized Compose render. Activate a fresh installation
through the [authority runbook](../docs/openvox-infra.md#fresh-initialization),
then enroll its native agent. Package versions and selected Git revisions are
recorded under `/var/log/openvox-provisioning/atlas-bootstrap*`.

The ownership, networking and Sysbox changes above are scripted for both a fresh
base and an empty installation of the earlier minimal bootstrap. General
Docker installations and populated state are not automatically migrated.

## Atlas production publishing

### Fresh Atlas Git receiver

Before fresh authority initialization, prepare the public receiver once on Atlas.
Use the freshly bootstrapped checkout at the reviewed commit already published
on GitHub. Run Git as its owner, `user_l`; `sudo` is only needed for the one-time
directory and system Git trust setup. These commands require an absent receiver;
restore the existing receiver on recovery instead.

```bash
(
set -e
cd /opt/docker/puppet-control-repo
revision=$(git rev-parse HEAD)
receiver=/opt/git/openvox/control.git
test ! -e "$receiver"
sudo install -d -o user_l -g docker -m 2775 /opt/git/openvox
git clone --bare --config core.sharedRepository=0664 \
  https://github.com/Enucatl/puppet-control-repo.git "$receiver"
git --git-dir="$receiver" update-ref refs/heads/production "$revision"
chmod -R o+rX,o-w "$receiver"
sudo git config --system --add safe.directory "$receiver"
git switch -C production "$revision"
git config --local --replace-all remote.origin.pushurl git@github.com:Enucatl/puppet-control-repo.git
git config --local --add remote.origin.pushurl "$receiver"
git config --local --replace-all remote.origin.push refs/heads/production:refs/heads/production
git config --local remote.pushDefault origin
git config --local branch.production.pushRemote origin
)
```

The full public clone supplies the receiver's history without inheriting the
bootstrap checkout's shallow boundary. Public read/traverse permissions let
OpenVox's remapped container user read committed code; the working checkout and
its ignored secrets retain their existing private permissions. The explicit
initialization command then deploys code and installs the managed receive hook.

### Ordinary publication

The migration and Git routing setup are complete. From Atlas's control checkout
on `production`, ordinary `git push` publishes to both configured targets:

- `git@github.com:Enucatl/puppet-control-repo.git`, branch `production`.
- `/opt/git/openvox/control.git`, branch `production`.

```bash
cd /opt/docker/puppet-control-repo
git push origin
```

The receiver's managed post-receive hook deploys the pushed branch through
OpenVox. Git can accept refs even when deployment fails, and a no-op retry does
not rerun the hook. After every publication, verify both target refs and the
receipt against the intended revision:

```bash
revision=$(git rev-parse HEAD)
test "$(git ls-remote git@github.com:Enucatl/puppet-control-repo.git refs/heads/production | cut -f1)" = "$revision"
test "$(git --git-dir=/opt/git/openvox/control.git rev-parse refs/heads/production)" = "$revision"
test "$(docker --host unix:///var/run/docker.sock exec infra-openvox-1 cat /git/deploy.status /git/deploy.commit)" = "$(printf '0\n%s' "$revision")"
```

If the receipt fails, inspect the deployment error and repair or rerun the
receiver's deployment before treating the publication as complete. Updating two
Git targets is not a transaction; inspect both refs after any push error.
GitHub authentication uses the administrator's SSH agent.

Receiver preparation stores public code without starting containers or generating
authorities. Compose mounts it read-only at `/git/control.git`.
The separate external volume at `/git` remains writable for deployment status.
Container UID/GID `999:999` maps to host `100999:100999`; public read/traverse
permissions allow it to read the bare repository without ACLs or ownership
changes. r10k uses `file:///git/control.git` to avoid local-clone hard links
across ownership boundaries, and the image trusts exactly `/git/control.git`
through Git's `safe.directory` setting.

After publishing and building `infra-openvox:36001`, run
`bash proxmox/atlas-git-access-check.sh` on Atlas. It uses a disposable container
with UID/GID `999:999`, user remapping, no network, a read-only image/source and
temporary writable deployment/cache/code directories. It checks the numeric
mapping, denied source writes, writable deployment status and a real r10k
checkout of `production`. A temporary r10k configuration skips Puppetfile
processing for this offline code-access check; module installation is verified
separately during activation. It starts no OpenVox server and initializes no CA.

Fresh initialization prepares the writable deployment volume, performs initial
code deployment and installs the host hook. Subsequent receives dispatch into
the container to run r10k, generate Puppet types and refresh environment caches.
The container helper uses container paths and cannot run directly as a host hook.
Ordinary startup uses the native foreground command; it performs no hook setup.

## Rebuilding a development VM

Existing targets are refused unless recreation is explicit and the local VM's
name and exact provisioning ownership marker match. Template 9000 and unrelated
guests are protected. Target validation happens before destructive operations
and enrollment-token issuance; cleanup rechecks ownership before removing a
failed new guest. To repeat the designated builds:

```bash
bash proxmox/ubuntu-server-template.sh -r
bash proxmox/atlas-server.sh -r
```

`-r` stops, destroys and recreates the matching development VM, including its
disks. Use it only while the guest is disposable. **After Atlas acquires real
authority or application state, recovery must preserve that state; do not use
`-r`.** Run [atlas-promote.sh](atlas-promote.sh) on Proxmox before activation;
it replaces VM 210's development ownership marker with the authority marker and
writes `/etc/openvox-infra/promoted` in the guest. Provisioning then refuses to
recreate the authoritative VM, and bootstrap refuses further runs. Recreating
template 9001 also requires `-r` and its matching marker; it never authorizes
replacement of 9000.

## Enrolled workload and desktop guests

These paths require `.env` with `VAULT_ADDR`, `VAULT_TOKEN`, `LOG_DIR` and an
operator-authenticated public `OPENVOX_CA_CERT` bundle; set `VAULT_CACERT` when
Vault uses a private CA. Vault must permit issuance of exact-certname, single-use,
two-hour `puppet-enrollment` tokens. See [Puppet enrollment](../docs/puppet-enrollment.md).
Keep credentials outside version control. Docker workload provisioning also needs
`IPV6_PREFIX`, a three-hextet /48 prefix, and a hostname beginning with `a`–`f`.

Set `VAULT_ADDR=https://vault.home.arpa:8200` in the declared environment with
authenticated CA trust. The following are fresh provisioning examples; the
existing Aperture and Cerebrum identities must not be recreated:

```bash
# On proxmox.home.arpa; select an unused ID automatically.
bash proxmox/desktop.sh -n aperture -t 9001 --puppet-server openvox.home.arpa

# On proxmox-cortex.home.arpa; build its separate XFS template first.
bash proxmox/ubuntu-xfs-template.sh -i 9002 -b 9012
bash proxmox/cerebrum-server.sh

# An enrolled Docker workload host, separate from the Atlas base.
bash proxmox/docker-server.sh -n app1 -t 9001 -s openvox.home.arpa -c 8 -m 16384 -d 50G
```

Desktop defaults are eight cores, 8000 MiB RAM and 128 GiB disk. Both flows accept
an explicit `-i VMID`; desktop recreation additionally requires
`--rebuild -i VMID -n NAME`, while Docker uses `-r -i VMID -n NAME`.
Desktop's `-n` sets a short hostname; `-l LETTER` instead selects a random name.
Desktop provisioning reboots and verifies the short hostname/FQDN again.

Cerebrum uses the Docker workload path: VM 230, template 9002, eight cores,
32768 MiB final RAM, a thin 1000 GiB XFS disk and virtio graphics for initial
provisioning, with MAC `bc:24:11:4e:c0:23` reserved as `10.0.40.135`.
It enrolls against Atlas and installs authenticated system CA trust before
FreeIPA enrollment. The completed GPU/network handover and staging resources
are recorded in the [handover record](../docs/archive/migration/cerebrum-cutover.md).
The production guest uses serial graphics after GPU transfer. Its daemon retains disabled user namespace remapping to
preserve migrated numeric ownership; other Docker builds retain their existing
`default` remapping. The Docker provisioning failure path retains
the guest and writes private diagnostics so an enrolled identity can be repaired
in place.
See the [Cerebrum migration record](../docs/archive/migration/cerebrum-migration.md) for current
acceptance evidence and retained recovery data. Preserve Cognition VM 220 and
Complex VM 200 for rollback; neither may run conflicting GPU or container
networks alongside Cerebrum. The one-time Complex-to-Cognition migration tools
have been retired.

Aperture VM 310 on `proxmox.home.arpa` uses the desktop role with Atlas IPA,
Vault, monitoring and Kerberos NFS endpoints. Its catalog creates the user-owned
Documents directory and exports it read-only to Atlas. Fresh enrolled guests
need authenticated Atlas system CA trust and admission of their exact identity
in Vault's certificate-authentication policy. Provisioning does not transfer
user or application state; preserve that data through backups and recovery.
See the [completed Aperture cutover record](../docs/archive/migration/aperture-cutover.md)
for hardware ownership and retained recovery evidence.

The FreeIPA acceptance fixture also loads optional `.env` settings: set
`PUPPET_SERVER=openvox.home.arpa` there to test the new server. Its readiness check
uses the shared 30-minute timeout and retains the failed VM/snippet for diagnosis.

## Acceptance and recovery

Template, bootstrap, enrollment and cutover acceptance records are retained in
the [migration archive](../docs/archive/migration/README.md). The one-time Atlas
base acceptance harness has been retired. Current provisioning retains its
ownership checks, promotion guard and shared readiness checks. Use the
[Atlas recovery workflow](../docs/atlas-recovery.md) for established authorities;
keep rollback records and independent backups according to their retention policy.
