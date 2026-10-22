# Cerebrum XFS migration

## Current status

Migration started on 2026-10-22. Cognition remains the source; final cutover has
not occurred. The replacement is `cerebrum.home.arpa`, VM 230 on
`proxmox-cortex.home.arpa`, using separate XFS template 9002. The requested final
allocation is eight cores, 32768 MiB RAM and a thin 1000 GiB disk. Staging uses
16384 MiB on each workload VM; the disposable builder 9012 uses 2048 MiB.

The controlling Codex process, checkout, session state and detached jobs run on
Atlas. The checkout and private evidence directory
`/home/user/.local/state/cerebrum-migration` use Atlas's local ext4 disk. Cortex
also keeps provisioning sources and logs under `/root/cerebrum-provisioning`.
Neither location depends on Cognition mounts.

## Source inventory and staging evidence

Cognition VM 220 was restarted at 16384 MiB. The guest reported
`MemTotal: 16301300 kB`; live Proxmox `maxmem` was 17179869184 bytes. Wolf was
stopped before this restart. Complex VM 200 remains stopped and its
`pre-cognition-20261020` snapshot remains held by `cognition-rollback`.

The source root is ext4, with 582458736640 bytes used. Persistent roots include
`/home` (376275456000 bytes), `/etc/wolf` (179879804928 bytes), `/opt/docker`
(2433470464 bytes), `/var/lib/wolf` and all 16 plain local Docker volumes
(11014230016 bytes). Preserve numeric ownership, ACLs, xattrs, hard links,
sparse allocation and hidden directories. Qdrant storage and extraction
evidence are inside `/home/user/zanzara-archive/.git`; model caches under
`/home/user/.cache` are retained data.

Compose projects are `/opt/docker/{wolf,vllm,vllm-qwen3.5-nvfp4-5090}` and
`/home/user/{zanzara-archive,fish-speech,barbero-scripts}`. Only Wolf had an
existing container and cached image. Preserve its digest
`sha256:b0ef253739f9bd49b71eba7e150c955fd05990c0d409e666d89236995351551a`.
Qdrant declares `v1.19.1`; Paddle services declare image digests. Other dormant
services retain their existing image declarations, including mutable tags whose
previous image layers no longer exist on the source.

The dormant Barbero project references `/scratch/archive/barbero-english/published`,
but `/scratch` is absent on Cognition. The authenticated `/export/scratch` mount
continues to use Atlas. The source exports `/opt/docker` through Kerberos NFS.

Cortex's mirrored NVMe `rpool` initially reported 789 GiB physical free space.
VM 220's ZFS volume uses 16 KiB blocks, LZ4, AES-256-GCM, deduplication off and
zero refreservation. No pool deduplication or ARC changes are needed.

DNS/DHCP now reserves Cerebrum at `10.0.40.135`, MAC `bc:24:11:4e:c0:23`,
ULA `fd6b:d722:1738:28:be24:11ff:fe4e:c023`. The repository DNS deployment and
router smoke checks passed. Container routes still point to Cognition.
Vault's exact agent-name admission includes Cerebrum while retaining Cognition.

## Template procedure

See [the reproducible XFS template procedure](../proxmox/README.md).
The root archive is pinned separately from the disk image to SHA256
`ccd08a7a76cdc69034dd2682b4913bdd051cb71ad87d0b66689015aa068e61c8`.
Canonical's signed manifest was verified using the installed Ubuntu cloud-image
keyring; signer fingerprint `D2EB44626FDDC30B513D5BB71A5D6C4C7DB87C81`.
Authenticated manifests are retained in the private evidence directory.

The completed template boots and reboots through UEFI with fresh NVRAM. Its
XFS defaults report `crc=1`, `finobt=1`, `reflink=1`, `bigtime=1`,
`inobtcount=1`, automatic allocation groups and an internal journal. Root mounts
with `noatime,inode64` and without continuous discard; `fstrim.timer` is enabled.
The SCSI disk uses VirtIO SCSI single, IO Thread, discard and SSD emulation.
QEMU confirms default direct/no-cache I/O, `io_uring` and a native ZFS volume.

The initial small-template expansion produced 400 allocation groups and a
64 MiB journal. Before copying data, the template is being rebuilt at the final
1000 GiB virtual size so XFS chooses geometry for that capacity. No allocation
group count, group size or journal size is manually configured. The target
root partition is 1022462 MiB, leaving EFI, boot and GPT/alignment space.
Both ZFS reservation properties must be zero before the builder starts.
Clones of this template must be at least 1000 GiB.

Ubuntu 26.04 does not publish the Curtin deb in its configured archive. The
builder uses checksum-pinned Canonical source instead. Hardware serial
discovery validates the unique blank target before supplying its device path
to Curtin; Proxmox's by-id names do not expose that serial.

## Cutover and rollback requirements

Before cutover, validate fresh UEFI boot and reboot, cloned machine/SSH identity,
cloud-init expansion, guest agent, XFS reflink/ftype, Docker OverlayFS and
disposable deduplication with independent writes. Keep monthly deduplication
disabled until the game data is present.

Quiesce source services and controllers, perform the final selective state sync
and comparison, then stop both guests for GPU handover. Move both
`0000:01:00.0` and `0000:01:00.1`. Never run both guests' production container
networks together. Switch all six current routes (GUA prefixes
`2a02:168:6271`, `2a02:168:6278` and ULA `fd6b:d722:1738`, each with
`c000::/56` and `c100::/64`) from next hop `fe80::be24:11ff:fe4e:c022` on
`eth3` to Cerebrum's `fe80::be24:11ff:fe4e:c023`. Update both controllers,
their VM permissions, Aperture's NFS server and external hostname dependencies.
Increase Cerebrum to 32768 MiB only after Cognition is stopped.

Rollback retains Cognition's disks and native identity. First stop controllers
and all replacement application writers. Preserve or reverse-sync replacement
application writes into a separate recovery copy; a blind rollback would lose
those writes. Review application database consistency before selectively
restoring state to Cognition. Stop Cerebrum before returning both GPU functions
and all six routes to Cognition. Restore controller targets/permissions and NFS
client configuration, reduce Cerebrum RAM before starting a 32 GiB Cognition,
then verify the source services. Never copy Cerebrum's OS keytabs, host keys,
Puppet certificates or enrollment files over Cognition's native identity.

Complex, Cognition, their recovery assets and held snapshots require explicit
approval before deletion. They remain local rollback assets rather than an
independent backup. Actual Moonlight streaming requires a client check.
