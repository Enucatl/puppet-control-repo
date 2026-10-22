# Cerebrum XFS migration

## Completed result

The infrastructure handover completed at 12:28 UTC on 2026-10-22.
`cerebrum.home.arpa`, VM 230 on `proxmox-cortex.home.arpa`, now has both GPU
functions, the routed container networks, eight cores, 32768 MiB RAM and a thin
1000 GiB XFS disk cloned from separate template 9002. Cognition VM 220 remains
stopped with its disks and native identity intact. Staging used 16384 MiB on
each workload VM; disposable builder 9012 used 2048 MiB.

Migration and automated acceptance completed on 2026-10-22. Real-data
deduplication, CUDA, Qdrant, extraction/orchestration, Wolf identity/encoders,
production reboot and controller wake/unlock/start checks passed. The user
confirmed Moonlight streaming works after re-pairing the client. Preserving the
server UUID and pairing records did not avoid re-pairing in this migration.

The controlling Codex process, checkout, session state and detached jobs run on
Atlas. The checkout and private evidence directory
`/home/user/.local/state/cerebrum-migration` use Atlas's local ext4 disk. Cortex
also keeps provisioning sources and logs under `/root/cerebrum-provisioning`.
Neither location depends on Cognition mounts.


The user also confirmed Paperless AI OCR processing through Cerebrum. Its local
ignored `.env` in `/opt/docker/paperless-ai` was corrected from
`http://complex.home.arpa:8108` to `http://cerebrum.home.arpa:8108`, and only its
AI container was recreated. Real parsing requests returned HTTP 200 and the OCR
queue progressed from 14 to 7, with five jobs entering metadata. Chat and metadata
continue to use OpenRouter. A transient startup health timeout cleared after
warmup. Extraction was then stopped at the user's request before VM shutdown.

The temporary Wolf target overrides and Cognition rebuild launcher were retired
after acceptance. Source node/role identity, DNS, native enrollment admission and
old-hop route cleanup remain for rollback; no VM, volume, snapshot or protected
recovery evidence was deleted. Reusable XFS provisioning, the pinned vLLM image
override and monthly Steam deduplication remain operational configuration.

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
router smoke checks passed. All six container routes now point to Cerebrum's
`fe80::be24:11ff:fe4e:c023` on `eth3`, including both retained/current ISP
prefixes and ULA.
Vault's exact agent-name admission includes Cerebrum while retaining Cognition.

## Template procedure

See [the reproducible XFS template procedure](../../../proxmox/README.md).
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
64 MiB journal. Before copying data, the template was rebuilt at the final
1000 GiB virtual size. The final template and clone have four allocation groups
and a 127807-block internal journal (about 499 MiB). No allocation
group count, group size or journal size is manually configured. The target
root partition is 1022462 MiB, leaving EFI, boot and GPT/alignment space.
Both ZFS reservation properties must be zero before the builder starts.
Clones of this template must be at least 1000 GiB.

## Copy and acceptance evidence

The staged copy completed at 11:31 UTC on 2026-10-22, including all 16 volumes,
application trees, homes and private recovery trees. The new root reported
about 561 GiB used and 438 GiB available; Cortex retained about 309 GiB free.
The copy preserves numeric IDs, ACLs, xattrs, hard links and sparse files.
Native home mTLS and the cloud administrator's SSH configuration are excluded
from restoration. Docker layers/databases are recreated separately.

After source services and controllers were quiesced, the final copy and
checksum comparison completed at 12:13 UTC. All selected persistent roots and
all 16 Docker volumes reported zero checksum differences. Private source OS
recovery copies were retained rather than applied over Cerebrum's fresh
enrollment, host keytabs, SSH keys or certificates.

At 11:37 UTC the corrected acceptance script verified disposable reflinks,
whole-file deduplication, independent writes, separate inodes and Docker's
containerd OverlayFS snapshotter. The 16 MiB deduplication trial completed in
2.01 seconds with 14540 KiB peak RSS. An earlier acceptance launcher consumed
its remaining input through nested SSH; those skipped checks were rerun.
The Wolf image was transferred through Docker save/load and its image ID
matched the source. No application container was started during that staging
check.

Independent checks passed native SSH/DNS, distinct SSH identities, Kerberos
host authentication, an Atlas NFS ticket/read with `sec=krb5p`, OpenVox chain
validation, native Cerebrum mTLS access and fresh Loki ingestion. Cortex still
has no configured backup job, matching the source's existing protection gap
(issue 22); local rollback VMs and held snapshots are not independent backups.

After handover, native Puppet convergence, identities, NFS and Loki checks
passed with the GPU and final 32768 MiB allocation. NVIDIA container CUDA
computation passed. Qdrant restored six collections with 3829 points; a
disposable write/read/delete probe passed. All four extraction services became
healthy, BGE returned 1024-dimensional embeddings, and five PDF fixtures passed,
including a 41-page document; the invalid-input probe returned HTTP 422.
The real weekly orchestration controller passed a zero-duration-window run,
cleaned up the services it started and left the already-running VM up.

Wolf retained UUID `06a7b1a9-0a90-49e4-9055-a589f7d49940`, both paired
clients and all three profiles. Its HTTP server-info response retained the UUID.
Loki recorded NVIDIA H.264, H.265 and AV1 `nvcodec` selection with zero-copy on
`/dev/dri/renderD128`. Optional VAAPI/MSDK/NVRTC/GStreamer probe warnings preceded
successful NVIDIA selection. The subsequent user test confirmed Moonlight
streaming works, but required re-pairing. The copied UUID and pairing records
therefore did not prove seamless client authentication. Clients using the old
host address must select Cerebrum; preserved pairing does not redirect DNS.

Atlas's live Wolf controller belongs to Compose project `infra` and retains
image `sha256:cacca5bd3fe44a62034b03f3fba9360bf423a3300fb0e25b07f453e85dd2bc65`,
external state volume `infra_wolf_state`, secrets and Traefik settings. Its saved
resolved Compose file had stale project/image assumptions; the actual container
labels and image were checked before recreating only this service with
`WOLF_GUEST_HOST=cerebrum.home.arpa` and `WOLF_VM_ID=230`. Other infrastructure
containers were not recreated or removed. The existing API token now has VM 230
permissions. Its health and authenticated Proxmox guest observation passed.

The preserved session deadline expired during migration, so controller restart
correctly performed its established VM/host timeout shutdown. At 13:13 UTC the
real controller then passed wake-on-LAN, unlock, VM 230 startup and Wolf startup.
It was restarted to load that accepted session into the live server. The
`migration-acceptance` session used the existing four-hour timeout. After user
acceptance, extraction and Wolf were stopped and Cerebrum was shut down at
13:33 UTC. The controller session was completed and its timeout cleared; the
controller remains idle and the weekly timer remains enabled.
Qdrant and extraction remain down between uses; no deploy watchers were added.
Post-wake native mTLS access to Loki and fresh ingestion passed.

The previous accepted vLLM image was recovered from the retained source
`/root/cognition-migration/image-pulls.log`. After the final comparison,
[cerebrum-vllm-override.yml](../../../proxmox/cerebrum-vllm-override.yml) was installed
as `/opt/docker/vllm/docker-compose.override.yml` so both BGE and
Qwen retain that digest rather than resolving `latest`. Acceptance used pinned
application images without unrelated upgrades. The optional legacy nightly/Ollama
stack remains dormant; its historical image versions were not recoverable.

External dotfiles commit `a77fe04ec154c6ec8c89c00045e2123b02a42717` changes the
local model endpoint to `http://cerebrum.home.arpa:8111/v1`. It was published
and fast-forwarded on Atlas and Cerebrum. Each dirty Codex configuration was
stashed separately and restored; byte comparisons proved that only this URL
changed. Safety stashes and private backups remain. Cognition was not updated.

Ubuntu 26.04 does not publish the Curtin deb in its configured archive. The
builder uses checksum-pinned Canonical source instead. Hardware serial
discovery validates the unique blank target before supplying its device path
to Curtin; Proxmox's by-id names do not expose that serial.

## Measured Steam deduplication

The Puppet-managed monthly `steam-dedup.service` scans only the two Steam
`steamapps` directories, using whole-file `duperemove`, an on-disk hash database,
one CPU thread, one I/O thread, low CPU/I/O priority and systemd's prevention of
overlapping oneshot runs. Its enforced limits are 2 GiB `MemoryMax`, 1 GiB
`MemoryHigh` and zero swap allowance. Failures and `/usr/bin/time -v` statistics
are visible in the journal. The monthly timer and periodic `fstrim.timer` are
enabled; continuous discard remains disabled.

The first production run completed in **24:59.17**, with **31756 KiB peak
process RSS** and **1076035584 bytes peak cgroup memory**, including charged
filesystem/cache memory. Root `df` used space fell from **605402583040** to
**529880997888 bytes**: **75521585152 bytes (70.33 GiB)** reclaimed inside the
guest across the two Steam trees. An incremental rerun completed in **1.02 s**,
with 14908 KiB peak RSS and 22224896 bytes peak cgroup memory; it reused the
hash database and scanned zero new file-content bytes.

Read-only FIEMAP measurements of both `common/dota 2 beta` trees found:

| Physical data measurement | Bytes |
| --- | ---: |
| Combined unique extents before deduplication | 154333175808 |
| Combined unique extents after deduplication | 79538565120 |
| Extents shared between both installations after deduplication | 74748530688 |

The two Dota installations now use **1.031 times the larger single
installation's physical data**, approximately one copy plus differing data.
This is extent sharing, with **7011 independent inodes and no cross-tree hard
links**; both installations remain separately writable. Disposable-file
independent-write checks passed. These figures exclude filesystem metadata;
ordinary `du` can count a shared extent twice and does not establish the unique
physical footprint.

After TRIM, the replacement ZFS volume's used space fell from 510339944448 to
477423841280 bytes, while pool free space rose from 330339909632 to
363258732544 bytes (about 30.66 GiB). The reported 998.5 GiB TRIM range is the
discard request size, not the measured physical saving. ZFS compression,
snapshots and retained source/template data explain why guest and host savings
need not match. ZFS deduplication remains off, ARC was not increased, and both
reservation properties remain zero.

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

The executed handover followed this order. Cognition's root and EFI volumes
have snapshots named `pre-cerebrum-20261022`, held by `cerebrum-rollback`.
Complex VM 200 and its earlier held snapshots were untouched. The production
reboot verified UEFI/cloud-init, XFS, guest agent, 32791780 kB guest MemTotal,
GPU availability and zero failed systemd units. Both predecessors remain stopped.

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
independent backup. Moonlight streaming was confirmed by the user after
re-pairing; no client streaming check remains open.
