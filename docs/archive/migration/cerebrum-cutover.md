# Cognition to Cerebrum handover

> Historical handover record. The migration is complete; do not replay this
> transaction. Rollback instructions and retention requirements remain applicable.

This procedure transferred the existing services to Cerebrum VM 230 on Cortex.
Cognition VM 220 and Complex VM 200 remain intact for rollback. Complete XFS
template, identity, storage and initial-copy validation before this handover.
Keep Cognition's node data and DNS records available for recovery.

## Hostname and VMID references

Active configuration targets `cerebrum.home.arpa`, VM 230:

| Dependency | Repository location |
| --- | --- |
| Provisioning and native host identity | `proxmox/cerebrum-server.sh`, `data/nodes/cerebrum.home.arpa.yaml`, `data/roles/nodes/cerebrum.home.arpa.yaml` |
| Wolf controller and token ACL | `docker/docker-compose.yml`, `docker/wolf/app/wolf.py`, `docker/wolf/scripts/10-proxmox-token.py` |
| Weekly vLLM orchestration | `data/nodes/proxmox.home.arpa.yaml`, `modules/proxmox_workflows/{files/vllm_weekly_window.py,manifests/vllm_weekly_window.pp}` |
| Authenticated NFS server | `data/nodes/aperture.home.arpa.yaml` |
| DNS, DHCP and six routed container networks | `provisioning/host_vars/router.yml`, `provisioning/templates/{app_configs,partials}` |
| Vault admission and shared host configuration | `proxmox/atlas-enroll.py`, `modules/profile/manifests/common/systemd.pp` |

Retain Cognition's DNS/DHCP records, node data, native enrollment admission,
and native identity for rollback. Retired provisioning and controller overrides
remain in Git history. Route deletion commands
must still name its old next hop. Historical migration records and source
recovery paths keep their original names. Aperture retains `/export/cognition`
and matching systemd unit names as stable client interfaces; their NFS server is
Cerebrum. Tests cover both the retained source and replacement identities.

Resolved controller configurations and copied user projects are additional live
state: inspect those separately, since changing repository defaults does not
rewrite their stored environment. The user dotfiles repository's local model
provider also needs its endpoint changed to `http://cerebrum.home.arpa:8111/v1`
while preserving unrelated local preferences.

## Coordinated handover

1. Stop Atlas's Wolf controller and Proxmox's weekly vLLM timer. Save their
   current resolved configurations privately, including Compose project name,
   volumes, secrets and overrides. Confirm neither controller can restart VM 220.
2. Quiesce source writers, stop source containers, and pause Puppet on both
   guests. Run the final selective copy and checksum comparison; preserve
   native Cerebrum SSH, FreeIPA, Vault and OpenVox identities. Copy application
   identities and all persistent volumes, including hidden home state.
3. Shut down both guests. Save both `qm config` outputs privately. Transfer
   both current RTX 5090 PCI functions from 220 to 230, retaining the original
   assignment strings. Set Cerebrum's display to `serial0`. Keep VM 220 stopped,
   then set VM 230 to 32768 MiB and verify effective memory after boot.
4. Publish the cutover configuration through the established Git receivers.
   Grant the existing Wolf user and token access to VM 230 without rotating
   their secret: on Cortex, run
   `WOLF_VM_ID=230 python3 docker/wolf/scripts/10-proxmox-token.py`.
5. Transfer all six container routes below while source networks remain down.
   Boot Cerebrum and verify its NIC link-local address before enabling workload
   networks. Never activate both guests' overlapping C-derived Docker pools.
6. Recreate only Atlas's controller service with its existing Compose project,
   state volume and secrets. Inspect the live Compose labels first: at this
   cutover the controller belonged to project `infra`, even though its saved
   resolved file named `wolf`. The production Compose environment now selects
   `cerebrum.home.arpa`, VM 230, without a migration override. For rollback,
   restore the saved resolved controller configuration and explicitly select
   `WOLF_GUEST_HOST=cognition.home.arpa` and `WOLF_VM_ID=220`.
   Converge the weekly controller on `proxmox.home.arpa` before restoring its
   timer. Preserve on-demand workload behavior and absent deploy watchers.
7. Converge Aperture's authenticated NFS mount to
   `cerebrum.home.arpa:/docker`, retaining `/export/cognition` as its client path.
   Drain active clients and unmount the old server before restarting the mount;
   changing the unit file alone does not migrate an established NFS connection.

The coordinated outage starts with source quiescence and ends after final copy,
GPU transfer, guest boot, routing and service acceptance. Measure the staged
copy delta before estimating its duration; a checksum comparison rereads all
selected data. Announce the concrete estimate before executing this sequence.

## Router routes

Read current state with
`/opt/vyatta/bin/vyatta-op-cmd-wrapper show configuration commands` and select
the C container routes. On 2026-10-22 all six routes below used Cognition's
`fe80::be24:11ff:fe4e:c022` hop on `eth3`. The new MAC ends in `c0:23` and its
expected EUI-64 hop is `fe80::be24:11ff:fe4e:c023`; confirm it in the guest.

Use VyOS configure mode (or the existing `vyos.vyos.vyos_config` workflow) to
apply this targeted transaction, then `commit` and `save`:

```text
delete protocols static route6 2a02:168:6271:c000::/56 next-hop fe80::be24:11ff:fe4e:c022
delete protocols static route6 2a02:168:6271:c100::/64 next-hop fe80::be24:11ff:fe4e:c022
delete protocols static route6 2a02:168:6278:c000::/56 next-hop fe80::be24:11ff:fe4e:c022
delete protocols static route6 2a02:168:6278:c100::/64 next-hop fe80::be24:11ff:fe4e:c022
delete protocols static route6 fd6b:d722:1738:c000::/56 next-hop fe80::be24:11ff:fe4e:c022
delete protocols static route6 fd6b:d722:1738:c100::/64 next-hop fe80::be24:11ff:fe4e:c022
set protocols static route6 2a02:168:6271:c000::/56 next-hop fe80::be24:11ff:fe4e:c023 interface eth3
set protocols static route6 2a02:168:6271:c100::/64 next-hop fe80::be24:11ff:fe4e:c023 interface eth3
set protocols static route6 2a02:168:6278:c000::/56 next-hop fe80::be24:11ff:fe4e:c023 interface eth3
set protocols static route6 2a02:168:6278:c100::/64 next-hop fe80::be24:11ff:fe4e:c023 interface eth3
set protocols static route6 fd6b:d722:1738:c000::/56 next-hop fe80::be24:11ff:fe4e:c023 interface eth3
set protocols static route6 fd6b:d722:1738:c100::/64 next-hop fe80::be24:11ff:fe4e:c023 interface eth3
```

The routing and deletion templates retain the same six-route result for future
applies, including the previous ISP prefix. Run the existing router healthcheck
workflow afterward, then verify routed workload reachability separately.

## External callers and acceptance

The upstream dotfiles repository owns the `ollama-complex` provider URL in
`dotfiles/codex/config.toml`. Change only its endpoint to
`http://cerebrum.home.arpa:8111/v1`, retaining the provider ID and unrelated local
edits. Puppet tracks upstream latest, so a guest-only edit will be overwritten.
Inspect copied application files for remaining old hostname/VMID dependencies
without printing `.env` contents or secrets.

Repeat the [previous acceptance checks](cognition-migration.md)
against the refreshed inventory: native identities and TLS, Puppet convergence,
Kerberos NFS, monitoring, backup configuration, Docker/NVIDIA computation, Wolf
UUID/pairings/encoders, Qdrant collections plus disposable write/read/delete,
extraction health and embedding/OCR requests, and the real weekly controller
with a zero-length window. Qdrant data lives under
`/home/user/zanzara-archive/.git/zanzara-qdrant`; preserve that hidden tree.
Run monthly deduplication through `steam-dedup.service`, recording runtime,
peak memory and `df` savings, then verify independent disposable writes and
TRIM. Actual Moonlight streaming requires a client check.

## Rollback

Stop both controllers and quiesce Cerebrum before rollback. Preserve all new
application writes in a private recovery copy; choose explicitly whether to
reverse-sync those application trees/volumes to Cognition or retain them for
later reconciliation. Never copy Cerebrum's host credentials over Cognition.

Shut down both guests; move the saved PCI assignments back to VM 220. Reduce
Cerebrum to 16 GiB before restoring Cognition to 32 GiB, checking host RAM and
effective guest allocations. Reverse the route transaction by swapping the
`c022` and `c023` hops, using `eth3` for both. Revert the cutover repository
changes so later applies preserve those routes and controller targets. Restore
Atlas's saved controller configuration and weekly target VM 220, and transition
Aperture's mount server back after draining/unmounting active clients. Boot
Cognition alone, verify services, then restore controllers. Keep Cerebrum's
disks and its recovery copies intact until new writes have been reconciled.
