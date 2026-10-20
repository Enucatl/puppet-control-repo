# Cognition staging and cutover

Cognition is VM **220** on `proxmox-cortex.home.arpa`, with **16384 MiB**
RAM, a **1000 GiB** disk and virtual graphics. Complex VM **200** remains the
GPU owner and production service host during staging. Keep Cognition's Docker
deploy project list empty until the final state and GPU handover. The cutover
configuration enables Wolf and vLLM watchers; vLLM explicitly selects extraction
because its Compose project has no default services.

## Cutover record — 2026-10-20

Cognition is now the active guest. Complex is stopped, with its root and EFI
snapshots named `pre-cognition-20261020` held by `cognition-rollback`.
Cognition retains the requested 16 GiB RAM and 1000 GiB disk; serial graphics
replace virtual graphics after transferring both RTX 5090 PCI functions.

The final frozen-source copy includes application bind mounts, homes, Wolf
pairing/game state and all 11 local Docker volumes. A final rsync comparison
found no differences. Source administrator and OS configuration are retained in
private recovery trees rather than applied over Cognition's native identities.
Docker image layers and runtime caches were recreated. Sparse game files retain
their sparse allocation.

All six existing container IPv6 routes now use Cognition's link-local next hop
on `eth3`. Atlas's Wolf controller targets `cognition.home.arpa`, VM 220; its
existing resolved Compose project is `wolf` despite the `infra-wolf-1` container
name. The weekly vLLM controller also targets VM 220, and its timer is restored.
OpenVox is enabled and converged; Wolf and vLLM deploy watchers are waiting.

Verified acceptance:

- Qdrant uses the original pinned image, with all six collections green and
  3,829 points retained. A disposable point write/read/delete check passed.
- NVIDIA container CUDA computation passed. Wolf retained its server UUID and
  both paired clients, and selected NVIDIA H.264/H.265/AV1 encoding.
- All four extraction services passed their health checks. BGE returned a
  1024-dimensional embedding, and PaddleOCR recognized text from a test image
  through the port 8108 gateway within the 16 GiB RAM budget. The actual weekly
  controller completed a zero-length window, waited for healthy extraction
  services and removed them afterward while retaining Cognition and Qdrant.
- Cloud-init, native administrative SSH/TLS identity, authenticated Atlas NFS,
  Alloy ingestion, and routed Docker bridge IPv6 reachability passed.

Final idle state has only Qdrant running, about 359 GiB free disk and 13.8 GiB
available RAM. Wolf and extraction remain on demand. Actual Moonlight streaming still requires a client
check. Retain Complex and its held snapshots until the rollback retention gate
below is satisfied; they share Cortex's storage and are not an independent
backup.

## Resume an interrupted first boot

The workload provisioner retains a failed guest and its private diagnostics.
Repair the versioned configuration and deploy it to Atlas before retrying.
With the existing OpenVox certificate retained, run as root on Cognition:

```bash
bash /path/to/proxmox/cognition-resume.sh
```

The helper verifies the guest identity, makes the original Hashicorp key import
safe to repeat, reruns cloud-init's user scripts, and checks the final service
and cloud-init result. It preserves the guest's enrollment and instance state.
Do not rebuild or clean cloud-init to repair a failed catalog.

## Persistent state

`cognition-copy-state.py` runs with sudo on Cognition and connects to Complex
as `user_l` using `sudo -n` for source reads and rsync. Both guests need rsync.
From the operator workstation, obtain Complex's SSH host key through the
authenticated Cortex guest agent and install that verified key for Cognition's
root SSH client:

```bash
ssh root@proxmox-cortex.home.arpa 'qm guest exec 200 -- cat /etc/ssh/ssh_host_ed25519_key.pub' |
  jq -er 'select(.exitcode == 0) | "complex.home.arpa " + ."out-data"' > /tmp/complex.known_hosts
cat /tmp/complex.known_hosts | ssh user_l@cognition.home.arpa \
  'sudo -n install -d -m700 /root/.ssh && sudo -n tee -a /root/.ssh/known_hosts >/dev/null'
ssh -A user_l@cognition.home.arpa
```

The copy uses the operator's forwarded SSH agent; no private key is copied.
On Cognition, check source access before the transfer:

```bash
sudo --preserve-env=SSH_AUTH_SOCK ssh -oBatchMode=yes -oStrictHostKeyChecking=yes \
  user_l@complex.home.arpa 'sudo -n true && command -v rsync'
```

The helper copies `/opt/docker`, `/home`, `/etc/wolf`, `/var/lib/wolf` and
every local Docker named volume. It preserves numeric ownership, ACLs, xattrs,
hard links and dotfiles, and does not copy Docker's runtime database or layers.
It retains Cognition's existing `/home/*/.config/home-arpa/mtls` identities
instead of importing Complex's client certificates.
It also keeps Cognition's `/home/user_l/.ssh` administrative access intact;
Complex's corresponding SSH state is copied separately to
`/root/cognition-migration/source-admin-ssh`.
Source `/root` is retained separately under
`/root/cognition-migration/source-root`, preserving credentials and unmanaged
administrator files without overwriting Cognition's root account.
The source `/etc` (excluding separately restored Wolf data), `/opt/vault`,
`/opt/gow`, `/var/backups` and `/var/spool` are retained privately under
`/root/cognition-migration/source-system`, preserving old host configuration,
TLS material and queued mail for review without applying old host identities.

The source contains roughly 350 GiB in `/home/user` and 176 GiB in `/etc/wolf`;
allow enough time for the first copy. Copies go directly to their destination,
so restoring does not require a second complete copy on the 1000 GiB disk.
Wolf's game files contain over 2 TiB of sparse logical data; rsync preserves
sparse allocation so these files fit in their existing physical footprint.
The transfer compresses sparse zero regions to avoid sending terabytes of zeros.

Pause Puppet and any deploy watchers on Cognition while copying, keep its
containers stopped, and run the initial copy when wanted:

```bash
sudo --preserve-env=SSH_AUTH_SOCK python3 /path/to/proxmox/cognition-copy-state.py
```

This initial copy is staging data from a live source. Before starting any
application, stop the Wolf controller and vLLM weekly timer so neither can
restart Complex or start a competing stack. Stop Complex deploy watchers and
all its containers, ensure no remaining application writer is active, and rerun:

```bash
sudo --preserve-env=SSH_AUTH_SOCK python3 /path/to/proxmox/cognition-copy-state.py --final
```

The final copy refuses running source or target containers and deletes stale
files only within each copied destination tree/volume. `/root` remains an
isolated recovery copy. The script refuses unhandled container bind mounts
outside the copied roots, managed TLS/timezone files and runtime device/socket
locations. `/export/scratch` is shared NFS state; use Cognition's
Atlas-backed mount and do not copy it as a local tree. Do not restore old host
keytabs, OS configuration, FreeIPA identity or OpenVox certificates from the
recovery copy. After the final copy, update copied application environment and
Compose files to Cognition/Atlas endpoints and regenerate host certificates.
Retain private backups of changed environment files.

Review copied Codex provider URLs as well. The optional `ollama-complex`
provider must use `http://cognition.home.arpa:8111/v1`; retain its provider ID.
Update `dotfiles/codex/config.toml` in the upstream dotfiles repository, since
`profile::dotfiles` tracks its latest revision and can overwrite guest-only
edits. Preserve existing local dotfiles changes before updating that checkout.

The helper also refuses different Docker user-namespace modes. When namespaces
are enabled, it checks dockremap UID/GID ranges. Dormant mappings do not affect
the current migration with namespaces disabled on both guests. If active modes
or ranges differ, determine ownership conversion from each
application's actual `userns_mode` and container user before copying; preserving
numeric IDs alone would leave application data inaccessible.

## GPU and routes

On Cortex, shut down Complex and Cognition, then run:

```bash
python3 /path/to/proxmox/cognition-gpu-handover.py to-cognition
qm set 220 --vga serial0
qm start 220
```

The helper verifies guest names, stopped state and an OVMF/q35 target. It records
both original VM configurations privately under `/root/cognition-migration`,
moves all `hostpci` assignments and restores the source assignment if the target
configuration fails. Both guests remain stopped until explicitly started.
Serial graphics replace virtio at cutover so the RTX keeps the `/dev/dri/renderD128`
path expected by the preserved Wolf configuration.

Move the six existing GUA/ULA container routes using the commands in
[Cognition staging and IPv6 route handover](../provisioning/README.md#cognition-staging-and-ipv6-route-handover).
They target Cognition's link-local address `fe80::be24:11ff:fe4e:c022` on `eth3`.
Do this only when the new guest owns the original container networks.

Verify `nvidia-smi` and an NVIDIA container before starting GPU workloads.
Then enable the `wolf` and `vllm` entries under
`profile::docker_host::git_deploy_projects` in Cognition's node data and apply
Puppet. Do not enable these entries during staging.

## Controllers and acceptance

The existing Proxmox token bootstrap supports a target VM without token rotation.
On Cortex, grant the existing Wolf user/token access to Cognition:

```bash
WOLF_VM_ID=220 python3 docker/wolf/scripts/10-proxmox-token.py
```

For Atlas's Wolf controller, add
`docker/wolf/cognition-controller.yml` after its existing Compose files when
recreating only `wolf`. It sets `WOLF_GUEST_HOST=cognition.home.arpa` and
`WOLF_VM_ID=220`; preserve Atlas's current resolved override, TLS and secrets.
For the weekly vLLM controller on `proxmox.home.arpa`, set:

```yaml
profile::vllm_weekly_window::vm_host: cognition.home.arpa
profile::vllm_weekly_window::vm_id: 220
```

Apply Puppet there before reenabling the timer. These parameters retain Complex
defaults until explicitly changed. Confirm Wolf pairing/game state and a vLLM
request, plus authentication, NFS, observability and protected recovery copies.
Routine Cortex backup automation remains tracked in
[issue #22](https://github.com/Enucatl/puppet-control-repo/issues/22), as agreed
in the migration plan; local rollback snapshots do not provide an independent
backup.

## Rollback

Stop controllers and Cognition containers and preserve new writes before
rollback. Disable Cognition's Puppet agent and Wolf/vLLM deploy watchers, and
restore its `profile::docker_host::git_deploy_projects` to `{}` before allowing
future convergence. Shut down both guests, then run the GPU helper with
`to-complex` and reverse the router commands using `eth3` for Complex as well.
Restore Atlas's saved resolved Wolf Compose configuration from
`/root/cognition-preparation/wolf-atlas-compose-before-cognition.json`, then
recreate only `wolf` with Compose project `wolf`. Revert the vLLM Hiera settings
and the live weekly service's VM host/ID before restarting Complex and its
controllers. Never run the original GPU/container networks on both guests. Keep Complex's disk intact for at least
14 days and one successful weekly backup cycle; reconcile any writes made on
Cognition before reverting application state.

The GPU recovery record deliberately prevents accidentally repeating the same
direction. After a completed rollback, archive the existing recovery records
before another planned attempt. No script deletes either VM or old source data.

Forbearance's legacy `/export/complex` automount uses the old Kerberos authority.
Its identity and mount transition remain part of the planned desktop migration;
do not import old host keytabs over Cognition's new identity to enable it.
