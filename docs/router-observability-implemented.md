# Router Observability Implementation

## Status

The router observability pipeline is partially implemented and wired into the repo. The current shape is:

- VyOS keeps local Alloy as the edge log collector.
- DHCP, DNS, and IPv6 NDP identity logs are collected on the router and shipped directly to Loki.
- Suricata is limited to the high-risk VLANs.
- VyOS exports unsampled IPFIX to `docker.home.arpa`.
- GoFlow2 runs as a container in `docker/docker-compose.yml` and emits flow records to stdout.
- The existing Alloy service on `docker.home.arpa` scrapes GoFlow2 container logs and performs GeoIP enrichment.
- GeoIP refresh is host-managed with `geoipupdate` on the Docker host, and it only activates when the two Vault-backed MaxMind secrets exist.

## What Was Implemented

### VyOS identity logging

- `dnsmasq` DHCP logging remains enabled and is still tailed by VyOS Alloy.
- AdGuard DNS query logging remains enabled and is still tailed by VyOS Alloy.
- A VyOS cron-style task now snapshots IPv6 neighbor state with `ip -6 neigh show`.
- The NDP snapshot task writes JSON lines to `/var/log/vyos-ndp/vyos-ndp.jsonl`, which VyOS Alloy ships as `job="vyos-ndp"`.
- The NDP log has its own persistent logrotate config under `/config/logrotate.d`.
- VyOS Alloy continues to ship DHCP, DNS, and NDP identity streams directly to Loki.

### VyOS flow export

- VyOS now exports IPFIX from the router interfaces that matter for visibility.
- The export is unsampled for now, so the router can be benchmarked before any sampling tradeoff is introduced.
- The router still keeps Suricata on IoT and Guest only; it is not being used as the router-wide flow source.

### Docker host collection and enrichment

- GoFlow2 is defined in [docker/docker-compose.yml](/opt/docker/puppet-control-repo/docker/docker-compose.yml).
- It receives VyOS IPFIX and writes decoded records to stdout.
- The host Alloy profile already present on `docker.home.arpa` was extended through Hiera in [data/nodes/docker.yaml](/opt/docker/puppet-control-repo/data/nodes/docker.yaml).
- That host Alloy instance now has a dedicated scrape/enrichment path for GoFlow2 container logs.
- Suricata EVE is also routed through that host Alloy path so GeoIP can be applied centrally.
- The central Alloy HTTP receiver binds to the Docker node LAN address, not every interface.
- VyOS Alloy uses a WAL on the central Suricata writer so a central Alloy outage does not immediately discard edge IDS events.

### GeoIP

- `geoipupdate` is installed on the Docker host, not in a container.
- The MaxMind credentials are read from the existing Vault-backed variables:
  - `profile::docker_host::maxmind_account_id`
  - `profile::docker_host::maxmind_license_key`
- If either secret is missing, the GeoIP updater path is not declared.
- The central enrichment config is also suppressed if either MaxMind secret is missing, keeping Alloy from starting with a missing GeoIP database dependency.
- The database is stored at `/var/lib/geoip`.
- Puppet runs one initial `geoipupdate` before Alloy is restarted, then keeps the database fresh with a weekly timer.
- The pipeline keeps country codes as labels for low-cardinality filtering and appends prefixed city-level GeoIP fields to the stored JSON log line.
- Private, multicast, loopback, link-local, documentation, ULA, and the locally delegated IPv6 prefix are skipped before GeoIP lookup.

## Important Files

- [docs/router-observability-plan.md](/opt/docker/puppet-control-repo/docs/router-observability-plan.md)
- [docs/router-observability-implemented.md](/opt/docker/puppet-control-repo/docs/router-observability-implemented.md)
- [data/nodes/docker.yaml](/opt/docker/puppet-control-repo/data/nodes/docker.yaml)
- [docker/docker-compose.yml](/opt/docker/puppet-control-repo/docker/docker-compose.yml)
- [modules/profile/manifests/docker_host.pp](/opt/docker/puppet-control-repo/modules/profile/manifests/docker_host.pp)
- [modules/profile/templates/GeoIP.conf.epp](/opt/docker/puppet-control-repo/modules/profile/templates/GeoIP.conf.epp)
- [modules/profile/templates/alloy.config.epp](/opt/docker/puppet-control-repo/modules/profile/templates/alloy.config.epp)
- [provisioning/templates/partials/system.j2](/opt/docker/puppet-control-repo/provisioning/templates/partials/system.j2)
- [provisioning/templates/app_configs/alloy-vyos.alloy.j2](/opt/docker/puppet-control-repo/provisioning/templates/app_configs/alloy-vyos.alloy.j2)
- [provisioning/templates/app_configs/vyos-ndp-snapshot.sh.j2](/opt/docker/puppet-control-repo/provisioning/templates/app_configs/vyos-ndp-snapshot.sh.j2)
- [provisioning/templates/app_configs/vyos-ndp-logrotate.j2](/opt/docker/puppet-control-repo/provisioning/templates/app_configs/vyos-ndp-logrotate.j2)

## Still Open

- Apply the changes on the actual VyOS and Docker host.
- Verify the Loki queries against live data.
- Run the unsampled IPFIX benchmark under real traffic.
- Decide later whether ASN should also be added to the stored record.
