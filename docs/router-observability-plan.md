# Router Logging And Flow Enrichment

## Summary

Build a Loki-based evidence trail for router identity, DNS, IDS, and flow activity without putting heavy enrichment work on VyOS.

- Keep Alloy on VyOS as the local log collector.
- Complete lightweight DHCP, DNS, and IPv6 NDP logging.
- Keep Suricata IDS/IDP only on high-risk VLANs: IoT and Guest.
- Add unsampled VyOS IPFIX export for router-wide flow telemetry, pending benchmark validation.
- Run a central Alloy collector on `docker.home.arpa` for Suricata and flow enrichment.
- Run GoFlow2 as a container from this repo’s `docker/` Compose stack.
- Avoid duplicate logs: every stream has exactly one path into Loki.

## Key Changes

- **DHCP and DNS**
  - Keep `dnsmasq` DHCP logging via `log-dhcp`.
  - Continue shipping `dnsmasq.log` and AdGuard query logs from VyOS Alloy directly to Loki.
  - Do not GeoIP-enrich DHCP or DNS logs.

- **IPv6 NDP snapshots**
  - Add a VyOS script under `/config/scripts` that periodically runs `ip -6 neigh show`.
  - Emit JSON events with timestamp, interface, IPv6 address, MAC address, neighbor state, and source marker.
  - Schedule it with VyOS task scheduler every 1-5 minutes.
  - Write NDP events to a dedicated JSON-lines log and ship them from VyOS Alloy directly to Loki under `job="vyos-ndp"`.

- **Suricata EVE**
  - Keep Suricata enabled only on IoT and Guest VLAN interfaces.
  - VyOS Alloy tails `/var/log/suricata/eve.json`, but does not write that stream directly to Loki.
  - VyOS Alloy sends only Suricata EVE to a central Alloy receiver on `docker.home.arpa`.
  - Central Alloy receives via `loki.source.api`, parses EVE JSON, enriches public `src_ip` and `dest_ip` with GeoIP, then writes once to Loki.
  - Bind the central receiver to the Docker node LAN address rather than all interfaces.
  - Keep an edge WAL on the VyOS-to-central writer so central collector outages do not immediately discard Suricata EVE.
  - Use low-cardinality labels only: `job`, `host`, `event_type`, and country codes. Keep IPs, ports, signatures, hostnames, ASN, and city out of labels, but attach prefixed city-level GeoIP details as Loki structured metadata.
  - The GeoIP database is refreshed on `docker.home.arpa` by host-managed `geoipupdate` on a weekly timer, writing to `/var/lib/geoip/GeoLite2-City.mmdb`.
  - Suppress GeoIP enrichment config until MaxMind credentials are present and run one initial update before restarting Alloy.

- **IPFIX flows**
  - Configure VyOS flow accounting to export IPFIX from router ingress interfaces to `docker.home.arpa`.
  - Start unsampled for benchmark testing.
- Add GoFlow2 as a container in this repo’s `docker/docker-compose.yml`.
- GoFlow2 receives VyOS IPFIX and writes decoded flow JSON to stdout.
- The existing Alloy service on `docker.home.arpa` scrapes the GoFlow2 container logs, enriches flow records with GeoIP, and writes them to Loki.
  - If unsampled IPFIX affects forwarding or storage too much, introduce sampling later as a measured follow-up.

## Queryability In Loki

- Use stable jobs:
  - `job="dnsmasq"`
  - `job="adguard"`
  - `job="vyos-ndp"`
  - `job="suricata"`
  - `job="ipfix"`

- Required analysis paths:
  - Reconstruct IPv4 ownership from DHCP logs at a timestamp.
  - Reconstruct IPv6 neighbor observations from NDP snapshots.
  - Query Suricata by `event_type`, GeoIP country, source/destination IP via `| json`.
  - Query IPFIX by source/destination IP, protocol, ports, byte counts, packet counts, interface, and GeoIP fields.
  - Correlate flow or alert timestamps with DHCP/NDP identity logs.

## Test Plan

- Run Ansible syntax check and VyOS diff before applying router changes.
- Confirm each stream appears once in Loki.
- Confirm Suricata no longer has a direct VyOS-to-Loki duplicate path.
- Confirm central Alloy enriches Suricata events with GeoIP data.
- Confirm VyOS exports IPFIX to GoFlow2.
- Confirm GoFlow2 emits decoded flow records under `job="ipfix"`.
- Benchmark unsampled IPFIX with large transfers while watching VyOS CPU, memory, interface drops, throughput, GoFlow2 resource use, and Loki ingest/storage growth.

## Assumptions

- `docker.home.arpa` is the right host for the Alloy collector, GoFlow2, MaxMind databases, and enrichment work.
- Loki remains the system of record.
- Six-month Loki retention may need adjustment after IPFIX volume is measured.
- GeoIP enrichment skips private, loopback, link-local, multicast, documentation, ULA, and locally delegated IPv6 ranges.
