# Cognition completed migration

> Historical migration record. Commands, status and acceptance limits below describe
> the recorded revision. For current operations, use the [archive index](README.md)
> to reach the provisioning, service and recovery runbooks.

## Cutover record — 2026-10-20

Cognition VM 220 on `proxmox-cortex.home.arpa` is now the active guest.
Complex VM 200 is stopped, with its root and EFI
snapshots named `pre-cognition-20261020` held by `cognition-rollback`.
Cognition was initially staged with 16 GiB RAM, then increased to 32 GiB after
cutover; its disk remains 1000 GiB. Serial graphics replace virtual graphics
after transferring both RTX 5090 PCI functions.

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
OpenVox is enabled and converged. Wolf and vLLM deploy watchers are removed;
their controllers start workloads on demand.

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

The initial 16 GiB idle state had only Qdrant running, about 359 GiB free disk
and 13.8 GiB available RAM. After the increase to 32 GiB, the guest has about
30 GiB available RAM. Wolf, extraction and the entire Zanzara/Qdrant stack are
on demand; leave the complete Zanzara Compose project down between uses,
preserving its volumes and bind-mounted data. Actual Moonlight streaming still
requires a client check. Retain Complex and its held snapshots until the
retention requirements recorded below are met; they share Cortex's storage and
are not an independent backup.

## Retained recovery evidence

The completed migration retired the state-copy, GPU handover and first-boot
recovery helpers. The former staging and cutover commands are no longer an
operational procedure.

Private recovery trees on Cognition retain source administrator and OS state:

- `/root/cognition-migration/source-admin-ssh`
- `/root/cognition-migration/source-root`
- `/root/cognition-migration/source-system`

Original GPU configuration records remain under
`/root/cognition-migration` on Cortex. Atlas's pre-cutover resolved Wolf Compose
configuration remains at
`/root/cognition-preparation/wolf-atlas-compose-before-cognition.json`.
These records do not replace application backups. Do not import old host
keytabs, OS configuration, FreeIPA identity or OpenVox certificates over
Cognition's native identity.

The cutover retention requirement was to keep Complex's disk intact for at
least 14 days and one successful weekly backup cycle. Reconcile any writes made
on Cognition before considering application-state recovery, and never run the
original GPU/container networks on both guests. Routine Cortex backup
automation remains tracked in
[issue #22](https://github.com/Enucatl/puppet-control-repo/issues/22); local
rollback snapshots do not provide an independent backup.

## Archived router handover notes

These notes were moved from the router runbook after Cerebrum replaced
Cognition. They describe the earlier Complex-to-Cognition transaction and
must not be replayed against the current routes.

### Cognition cutover record

Cognition VM 220 on Cortex reserves `10.0.40.134` for
`bc:24:11:4e:c0:22`; `cognition.home.arpa` and its reverse record use that
address. Its verified ULA `fd6b:d722:1738:28:be24:11ff:fe4e:c022` also has
forward/reverse DNS so IPv6 NFS clients match the server's `*.home.arpa` export
restriction. Apply it with the existing `--tags dns,healthcheck` workflow above.
The VM uses virtual graphics and a separate NIC during staging. Its link-local
address is `fe80::be24:11ff:fe4e:c022` on router interface `eth3`.
No container IPv6 routes move during staging.

At the coordinated container/GPU cutover, after Complex's workloads are stopped,
first update the four `c000::/56` and `c100::/64` declarations in
`templates/partials/nat_routing.j2` to use `cognition_node_ipv6_suffix` and
`cortex_interface`. Do not change the other Docker/desktop routes. This keeps
future full applies from returning the routes to Complex.

Then, on the router, move all existing versions of those six routes, including
the previous ISP prefix retained in the live configuration. The following were
verified on 2026-10-20; review `show configuration commands | match 'c[01]00'`
before applying if the ISP prefix changes:

```text
configure
delete protocols static route6 2a02:168:6271:c000::/56 next-hop fe80::be24:11ff:feb0:32c9
delete protocols static route6 2a02:168:6271:c100::/64 next-hop fe80::be24:11ff:feb0:32c9
delete protocols static route6 2a02:168:6278:c000::/56 next-hop fe80::be24:11ff:feb0:32c9
delete protocols static route6 2a02:168:6278:c100::/64 next-hop fe80::be24:11ff:feb0:32c9
delete protocols static route6 fd6b:d722:1738:c000::/56 next-hop fe80::be24:11ff:feb0:32c9
delete protocols static route6 fd6b:d722:1738:c100::/64 next-hop fe80::be24:11ff:feb0:32c9
set protocols static route6 2a02:168:6271:c000::/56 next-hop fe80::be24:11ff:fe4e:c022 interface eth3
set protocols static route6 2a02:168:6271:c100::/64 next-hop fe80::be24:11ff:fe4e:c022 interface eth3
set protocols static route6 2a02:168:6278:c000::/56 next-hop fe80::be24:11ff:fe4e:c022 interface eth3
set protocols static route6 2a02:168:6278:c100::/64 next-hop fe80::be24:11ff:fe4e:c022 interface eth3
set protocols static route6 fd6b:d722:1738:c000::/56 next-hop fe80::be24:11ff:fe4e:c022 interface eth3
set protocols static route6 fd6b:d722:1738:c100::/64 next-hop fe80::be24:11ff:fe4e:c022 interface eth3
commit
save
exit
```

Run `--tags healthcheck`, and check `ip -6 route get` plus connectivity to a
running migrated container in each prefix. Router smoke checks alone do not
verify workload reachability. For rollback, stop Cognition's workloads, reverse
the route commands (delete the Cognition next hop, set the Complex next hop),
and return the four template declarations to `complex_node_ipv6_suffix`.
Use `eth3` for Complex too: both VMs are on the Cortex DAC segment; the old
`eth1` route interface is not the correct interface for their link-local hops.
