# configure the router

## Active Technitium DNS and DHCP

Technitium 15.6.2 serves production DNS on port 53 and DHCPv4, using persistent
data at `/config/containers/technitium-candidate`, limited to 512 MiB RAM and
half a CPU. The web console listens on loopback port 5380.

Normal applies preserve existing DHCP scopes, reservations and imported lease
holds. Managed DNS settings, forwarders, records and blocks are reapplied
through the API; additional zones and records remain in place.

The `technitium-rebind` scheduled task checks every minute and rebinds only
enabled DHCP scopes with an available interface that remained bound to
`0.0.0.0`. This handles interfaces whose carrier arrives after server startup;
recovery may take until the next minute tick. It preserves DHCP settings.

Technitium logs reach Loki through the existing central Alloy receiver with
`{job="technitium",host="router.home.arpa"}`.

It uses the same four Quad9 upstreams, DNSSEC validation and enabled AdGuard
DNS filter URL. AdAway remains unused. Technitium's filtering, cache and rate
limits differ from AdGuard; the shared URL does not guarantee identical results.
The UDP rate limit is 6,000 queries/minute per IPv4 /32 or IPv6 /56.

Access the web console through SSH:

```bash
ssh -N -L 5380:127.0.0.1:5380 vyos@10.0.0.1
# Open http://127.0.0.1:5380; username: admin
ssh vyos@10.0.0.1 sudo -n cat /config/containers/technitium-candidate/admin-password
```

The generated password is stored root-only on the router. Check production DNS:

```bash
ssh vyos@10.0.0.1 dig @127.0.0.1 atlas.home.arpa
ssh vyos@10.0.0.1 dig @127.0.0.1 _apt_proxy._tcp.home.arpa SRV
```

### DNS and DHCP configuration

The replacement is declared in `vars/technitium.yml`.
Technitium owns `home.arpa`, the local IPv4 reverse zone and both IPv6 /48
reverse zones. It includes the existing static A/AAAA records, service aliases,
wildcard service names, canonical PTR records, filtering and apt-proxy SRV
record. It has no DNS forwarding dependency on dnsmasq.

`intranet.enucatl.com` mirrors names below `home.arpa` through one apex DNAME
record targeting `home.arpa`. This includes static services, wildcard names and
future DHCP registrations; DNS synthesizes CNAMEs for queried names below the
new suffix. SRV queries follow the same substitution and retain the existing
`home.arpa` service targets. Both forward zones use the same query ACL as the
reverse zones. Canonical PTRs and the DHCP domain remain `home.arpa`; this adds
service names without changing machine identities. Public DNS and ACME
certificate issuance are unchanged.

Local forward and reverse zones allow queries from LAN, trusted Wi-Fi, Cortex
and WireGuard clients, covering IPv4 plus ULA/public IPv6. Other sources are
denied, including IoT and guest clients. The single IoT exception is the
printer's reserved IPv4 address, `10.0.20.189`, which can resolve all local
names for its Samba workflow. No verified printer IPv6 address is declared,
so IPv6 remains denied for the printer too. Loopback queries remain allowed
by Technitium. Zone transfers are disabled. Public internet DNS resolution
is allowed for clients in `10.0.0.0/8` and the local ULA/public IPv6 /48s,
including IoT/guest. This explicit recursion policy also permits clients using
public IPv6 source addresses; it does not open recursion to arbitrary internet
clients. DHCP continues advertising
`home.arpa` and registering client hostnames on every scope.

| DHCP scope | Dynamic pool | Lease | Domain option | PXE |
| --- | --- | --- | --- | --- |
| LAN | 10.0.0.100–200 | 24 hours | home.arpa | iVentoy on Atlas |
| TrustedWiFi | 10.0.10.100–200 | 24 hours | home.arpa | None |
| IoT | 10.0.20.100–200 | 1 hour | home.arpa | None |
| Guest | 10.0.30.100–200 | 1 hour | home.arpa | None |
| Cortex | 10.0.40.100–200 | 24 hours | home.arpa | None |

All eight MAC reservations are retained. Technitium requires reservations
inside the scope bounds, so scopes start at their router's `.1` address and
exclude `.1`–`.99` from dynamic allocation. Reserved addresses can still use
that exclusion range. Infinite leases are unsupported: reserved clients use
their scope's finite lease duration. The address remains reserved to the MAC.
DHCP DNS updates are enabled for all five scopes, with conflicting
dynamic-client A-record overwrites disabled. Technitium couples automatic DNS
registration to the advertised domain, so IoT/guest now advertise `home.arpa`
too. Previously, dnsmasq registered their names through its global domain setting
while omitting the domain option on those two scopes. IPv6 SLAAC, router
advertisements and prefix delegation remain managed by VyOS; Technitium
adds no DHCPv6 service.

### Configuration and verification

Use `--tags dns` or `--tags technitium` to refresh DNS settings and install the
DHCP recovery task. `--tags technitium-monitoring` refreshes log shipping;
`--tags healthcheck` runs router smoke checks.

On a fresh router, run `--tags technitium-bootstrap` before the first ordinary
apply. Bootstrap creates and configures DNS and DHCP in an isolated container
before starting the production service. It refuses to run if `dns.config` or
the production `technitium` container already exists.

Configuration is rendered as JSON and applied by `src/router_technitium/`,
using the router entry point `files/technitium.py`. API sessions use the same generated administrator
password as the web console, stored root-only in the data directory.

The Python utility was deployed on 2026-10-26. All 88 router smoke checks passed,
the complete DHCP scope configuration checksum was unchanged, and the saved
recovery schedule uses Python. The one-time configuration deletes were removed
after application.

Run `tests/run-technitium.sh` for isolated real-server and configuration tests.
It removes its temporary containers. These tests cannot establish physical
client renewal or iVentoy PXE success.

Regression checks are in `tests/test_technitium.py`, using the provisioning
Ansible environment and pytest inside disposable Docker containers. The real
API check requires `TECHNITIUM_API_URL` pointing to an isolated test server;
it creates test DNS records and DHCP scopes and must not target production.
Source-address checks also use `TECHNITIUM_SOURCE_TEST=1`, `iproute2`, and
`NET_ADMIN` inside the isolated container to verify IPv4/IPv6 query policies.

## Setup

```bash
uv sync
```

## Testing progression

### 1 — syntax check (local only, no vault/router needed)
```bash
VAULT_CACERT=/etc/ssl/certs/ca-certificates.crt uv run ansible-playbook -i inventory/router.yml router.yml --vault-password-file vars/password --syntax-check
```

### 2 — diff (read-only, safe)
Fetches `show configuration commands` from the router, renders the template locally, and diffs both sides in set-command format.
```bash
VAULT_CACERT=/etc/ssl/certs/ca-certificates.crt uv run ansible-playbook -i inventory/router.yml router.yml --vault-password-file vars/password --tags diff
```

### 3 — dry-run apply (no commit)
Enters configure mode, loads the commands into candidate config, reports what would change, then discards.
```bash
VAULT_CACERT=/etc/ssl/certs/ca-certificates.crt uv run ansible-playbook -i inventory/router.yml router.yml --vault-password-file vars/password --tags apply --check
```

### 4 — apply
```bash
VAULT_CACERT=/etc/ssl/certs/ca-certificates.crt uv run ansible-playbook -i inventory/router.yml router.yml --vault-password-file vars/password --tags apply
```

For changes that only touch the declarative VyOS config, skip app files,
container image pulls, restarts, and smoke checks:
```bash
VAULT_CACERT=/etc/ssl/certs/ca-certificates.crt uv run ansible-playbook -i inventory/router.yml router.yml --vault-password-file vars/password --tags vyos_config
```

### 5 — smoke checks
Runs automatically at the end of `--tags apply` and can also be executed on its own.
```bash
VAULT_CACERT=/etc/ssl/certs/ca-certificates.crt uv run ansible-playbook -i inventory/router.yml router.yml --vault-password-file vars/password --tags healthcheck
```

### 6 — rolling upgrade
Downloads the latest signed VyOS rolling ISO, verifies it with minisign, installs it, reboots, then runs the full deployment and smoke checks.
```bash
VAULT_CACERT=/etc/ssl/certs/ca-certificates.crt uv run ansible-playbook -i inventory/router.yml router.yml --vault-password-file vars/password --tags upgrade
```

## Workflow for any change

1. Edit a partial under `templates/partials/`
2. `--tags diff` to review the delta
3. `--tags vyos_config` to push router config only, or `--tags apply` for the full workflow
4. `--tags diff` again to confirm clean
5. `--tags healthcheck` if you want to re-run router smoke checks on demand
6. `--tags upgrade` when you need to move the router to the latest signed rolling release

## Atlas infrastructure DNS and DHCP

`host_vars/router.yml` reserves Atlas at `10.0.0.158` for MAC
`bc:24:11:4e:a4:9e`. The Atlas VM creation script keeps that MAC across rebuilds;
update both declarations together if the NIC reservation changes.
The Technitium declarations publish exact A and AAAA records for `atlas.home.arpa`,
`vault.home.arpa`, `openvox.home.arpa` and `ipa.home.arpa` at this address and Atlas's verified
EUI-64 ULA `fd6b:d722:1738:0:be24:11ff:fe4e:a49e`. The ULA is retained across
ISP prefix changes; the pinned NIC MAC preserves its interface identifier on
rebuild. Reverse DNS remains
`atlas.home.arpa`. Legacy service aliases `freeipa.home.arpa` and
`hcv.home.arpa` now resolve to Atlas over IPv4 and IPv6; the retired Docker
host's own record remains unchanged. Vault uses `vault.home.arpa` on Atlas.

The `atlas.home.arpa` suffix also resolves application subdomains such as
`traefik.atlas.home.arpa` and `authelia.atlas.home.arpa` to Atlas over IPv4 and
IPv6, using the same wildcard names as `docker.home.arpa`. Exact
infrastructure records and Atlas's canonical PTR remain in place. Application
certificates and Traefik routes must cover these names before use.

Apply only DNS/DHCP and its smoke checks with:

```bash
VAULT_CACERT=/etc/ssl/certs/ca-certificates.crt uv run --frozen ansible-playbook -i inventory/router.yml router.yml --vault-password-file vars/password --tags dns,healthcheck
```

With Technitium active, this path reapplies its DNS declarations and updates
the smoke script while preserving DHCP scopes and continuity reservations.
Full `--tags apply` includes the same declarations. Smoke checks query production
DNS over IPv4/IPv6, including infrastructure and application names and Atlas's
canonical PTR record.

These records publish the active service endpoints: `https://vault.home.arpa:8200`,
direct TLS `openvox.home.arpa:8140` and FreeIPA at `ipa.home.arpa`. Service certificates must contain the
corresponding DNS names; Atlas's native agent identity remains `atlas.home.arpa`.
DNS publication does not initialize or activate Vault/OpenVox/FreeIPA.

Historical DNS cutover measurements and frozen acceptance records are preserved
in Git history. For current behavior, run the router smoke checks.
FreeIPA is active on Atlas; see the [current service runbook](../docs/atlas-freeipa.md).

### Required secrets

Active secrets (`ipv6_prefix`, `snmp_v3_password`, `wg_server_private_key`, `cloudflare_api_token`) are pulled at runtime from HashiCorp Vault (`vault kv get kv/puppet`).

The rolling upgrade workflow uses the official nightly-builds page and verifies images with minisign before installation.

### TeamSpeak forwarding

UDP 9987 targets Atlas in NAT rule 30 and IPv4/IPv6 firewall rules 49/50.
Apply only those rules and the router smoke checks with:

```bash
VAULT_CACERT=/etc/ssl/certs/ca-certificates.crt uv run --frozen ansible-playbook -i inventory/router.yml router.yml --vault-password-file vars/password --tags teamspeak,healthcheck
```

Atlas's `tor-i2pd` DDNS updater owns `teamspeak.enucatl.com` and
`torrent.enucatl.com`; Docker's updater is stopped and guarded. Atlas's updater remains behind the
`migration-handover` Compose profile during preparation; its explicit cutover
activation prevents two hosts from updating the same DNS record.

### qBittorrent forwarding

TCP/UDP 6881 and 9000 target Atlas in NAT rule 10 and IPv4/IPv6 firewall
rules 29/30. Apply only those rules and router smoke checks with:

```bash
VAULT_CACERT=/etc/ssl/certs/ca-certificates.crt uv run --frozen ansible-playbook -i inventory/router.yml router.yml --vault-password-file vars/password --tags qbittorrent,healthcheck
```

### NetFlow collector

IPFIX exports from the IoT/guest interfaces go to Atlas UDP 2055. Apply only
the collector destination and router smoke checks with `--tags netflow,healthcheck`.
The collector runs as `goflow2` in the infrastructure project's root
`../compose.yaml` and has no persistent state.
Atlas's dedicated Alloy pipeline enriches flow records with GeoIP. Its local
Loki write URL uses the loopback-only published port 3100 because Alloy's Go
TLS client cannot negotiate the ML-DSA ingress; HTTPS reads retain mTLS.

### Container routing and logs

Atlas's public Docker pool `a400::/56` and default bridge `a500::/64` route
through `fe80::be24:11ff:fe4e:a49e` on `eth1`. Aperture's `a200::/56` and
`a300::/64` use `fe80::be24:11ff:fe60:9e28` on `eth1` in both public and ULA
address spaces. Cerebrum's `c000::/56` and `c100::/64` use
`fe80::be24:11ff:fe4e:c023` on `eth3` in both address spaces.
The historical route hops, firewall rules, log hostnames and previous ISP prefix
cleanup were confirmed absent on the live router on 2026-11-01. Retired Cognition
forward and reverse DNS records were also removed; Cerebrum records and Cortex
DHCP reservations were verified intact. All 80 current router smoke checks passed.

Router Alloy sends logs to the central Atlas Alloy receiver. The image is pinned
to v1.21.0. The deployment copies the controller's system CA bundle from
`/etc/ssl/certs/ca-certificates.crt` to persistent storage and mounts it read-only
at the same standard path inside Alloy; certificate verification remains enabled.
Reapply after controller trust updates to refresh the router's copy.
Suricata uses its enrichment API on port 12345. Smoke checks verify Alloy's HTTP
readiness and container state. Loki/Alloy static mappings use Atlas, and the PXE
boot server points at Atlas without activating iVentoy.

Use the full `--tags apply` workflow to install DNS and Alloy configuration,
apply routes and rules, restart containers, and run smoke checks.
Route-only `--tags vyos_config` does not update DNS or Alloy configuration.

The historical rejected log WAL was preserved in the root-only router archive
`/config/containers/alloy/migration-wal-replay-20261021.tar.gz`. Rejected history
is not confirmed complete in Loki; the archive has not been replayed.

### Cerebrum routing

The GPU workload host is `cerebrum.home.arpa`, VM 230, at `10.0.40.135`, MAC
`bc:24:11:4e:c0:23` and ULA `fd6b:d722:1738:28:be24:11ff:fe4e:c023`.
The route templates select `cerebrum_node_ipv6_suffix`. Its first hostname letter
determines its container pools; avoid activating those pools on another guest.
