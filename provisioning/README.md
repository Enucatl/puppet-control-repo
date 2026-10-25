# configure the router

## Staged Technitium DNS

Technitium 15.6.2 runs alongside AdGuard with persistent data at
`/config/containers/technitium`, limited to 512 MiB RAM and half a CPU.
DNS listens only on `127.0.0.1:5354` and `[::1]:5354`; the web console listens
only on loopback port 5380. DHCP, router DNS advertisements and production
AdGuard remain unchanged. No `intranet.enucatl.com` zone is created yet.

Deploy just this container from `provisioning/`:

```bash
VAULT_CACERT=/etc/ssl/certs/ca-certificates.crt uv run --frozen ansible-playbook -i inventory/router.yml router.yml --vault-password-file vars/password --tags technitium
```

Ansible renders and uploads `technitium-configure.sh`, then applies the shared
VyOS container template, using the same modules as the other router tasks.
The configuration script first uses an isolated network because Technitium defaults to
port 53. It sets loopback listeners before VyOS starts the host-networked
container. Each `--tags technitium` or `--tags apply` run reapplies the DNS
settings, forwarding records, rewrites and blocks from this repo through the
API. UI changes to those managed settings are overwritten on the next apply;
additional zones/records are left in place. Normal `--tags apply` also ensures
the container is staged before applying its config.

It uses the same four Quad9 upstreams, DNSSEC validation and enabled AdGuard
DNS filter URL. Local names and IPv4/IPv6 reverse zones forward to dnsmasq on
127.0.0.1:5300. The apt-cacher A/SRV rewrites, `.icu` block and diagnostic-name
blocks are translated into native records/blocked zones. AdAway remains unused.
Technitium's blocklist parser and cache/rate-limit behavior differ from AdGuard;
using the same URL does not guarantee identical filtering results. The rate
limit is approximately equivalent (6,000 UDP queries/minute per IPv4 /32 or
IPv6 /56 rather than AdGuard's 100/second). Query logs/stats retain one day and
are not shipped to Loki yet.

Access the web console through SSH:

```bash
ssh -N -L 5380:127.0.0.1:5380 vyos@10.0.0.1
# Open http://127.0.0.1:5380; username: admin
ssh vyos@10.0.0.1 sudo -n cat /config/containers/technitium/admin-password
```

The generated password is stored root-only on the router. Test DNS explicitly
without changing any clients:

```bash
ssh vyos@10.0.0.1 dig @127.0.0.1 -p 5354 atlas.home.arpa
ssh vyos@10.0.0.1 dig @127.0.0.1 -p 5354 _apt_proxy._tcp.home.arpa SRV
```

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
The dnsmasq template publishes exact A and AAAA records for `atlas.home.arpa`,
`vault.home.arpa`, `openvox.home.arpa` and `ipa.home.arpa` at this address and Atlas's verified
EUI-64 ULA `fd6b:d722:1738:0:be24:11ff:fe4e:a49e`. The ULA is retained across
ISP prefix changes; the pinned NIC MAC preserves its interface identifier on
rebuild. Reverse DNS remains
`atlas.home.arpa`. Legacy service aliases `freeipa.home.arpa` and
`hcv.home.arpa` now resolve to Atlas over IPv4 and IPv6; the retired Docker
host's own record remains unchanged. Vault uses `vault.home.arpa` on Atlas.

The `atlas.home.arpa` suffix also resolves application subdomains such as
`traefik.atlas.home.arpa` and `authelia.atlas.home.arpa` to Atlas over IPv4 and
IPv6, using the same dnsmasq wildcard pattern as `docker.home.arpa`. Exact
infrastructure records and Atlas's canonical PTR remain in place. Application
certificates and Traefik routes must cover these names before use.

Apply only DNS/DHCP and its smoke checks with:

```bash
VAULT_CACERT=/etc/ssl/certs/ca-certificates.crt uv run --frozen ansible-playbook -i inventory/router.yml router.yml --vault-password-file vars/password --tags dns,healthcheck
```

This path validates the staged configuration with the running dnsmasq image,
backs up the prior file to `/config/containers/dnsmasq/dnsmasq.conf.previous`,
installs the configuration and updated smoke script, then restarts dnsmasq and
AdGuard to clear cached DNS answers. It does not apply the declarative router
configuration or update other containers. Full `--tags apply` also includes
these DNS declarations. Smoke checks verify all four names through AdGuard
over IPv4/IPv6 and dnsmasq directly, plus the Traefik/Authelia application names
and Atlas's canonical PTR record.

These records publish the active service endpoints: `https://vault.home.arpa:8200`,
direct TLS `openvox.home.arpa:8140` and FreeIPA at `ipa.home.arpa`. Service certificates must contain the
corresponding DNS names; Atlas's native agent identity remains `atlas.home.arpa`.
DNS publication does not initialize or activate Vault/OpenVox/FreeIPA.

Live acceptance passed on **2026-10-19**, using revision
`b72af4ee85df77bb8b7f04c595cd421073e0e25b`: **47 router smoke checks passed**,
including A/AAAA answers for all three names through AdGuard over IPv4/IPv6 and
dnsmasq directly. Separate checks verified both PTR records, the MAC reservation
and the legacy service addresses. IPv6 connectivity to Atlas passed from the
workstation and router. See [sanitized DNS evidence](../docs/archive/migration/atlas-dns-results.json).
The fixed-MAC creation path passed mocked provisioning tests; a live VM rebuild
was not performed for this DNS change.
The `ipa.home.arpa` addition was deployed on **2026-10-19** with **53 router
smoke checks passed**. Separate checks confirmed all four endpoints' A/AAAA
answers over IPv4/IPv6, Atlas's IPv4/IPv6 canonical PTR records, and unchanged
legacy service addresses. See [FreeIPA DNS evidence](../docs/archive/migration/atlas-ipa-dns-results.json).
FreeIPA is active on Atlas; see the [current service runbook](../docs/atlas-freeipa.md).

## Secrets (vars/secrets.yml)

`vars/secrets.yml` is an Ansible Vault-encrypted file. The vault password is stored in `vars/password` (not committed). To recreate it:

```bash
rm vars/secrets.yml
uv run ansible-vault create vars/secrets.yml --vault-password-file vars/password
```

## Manual deletes

Add `delete ...` lines to `templates/partials/deletes.j2` for stale nodes that the set-command partials won't naturally overwrite. Remove the line once confirmed absent on the router.

### Required secrets

`secrets.yml` contains only `adguard_users`. The other secrets (`ipv6_prefix`, `snmp_v3_password`, `wg_server_private_key`, `cloudflare_api_token`) are pulled at runtime from HashiCorp Vault (`vault kv get kv/puppet`).

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

### Completed migration residuals

The current template routes `a000::/56` and `a100::/64` through Atlas, and
`a200::/56` and `a300::/64` through Aperture's verified link-local address
`fe80::be24:11ff:fe60:9e28` on `eth1`, for both the current ISP prefix and ULA.
The delete partial removes the previous next hops before installing these
routes. Review `show configuration commands | match 'a[0123]00'` before applying:
any retained previous ISP prefixes need the same next-hop replacement while
preserving their prefixes. The Cerebrum `c000`/`c100` routes use `eth3`; their
handover from Cognition is coordinated separately below.

Router logs use verified HTTPS to `loki.atlas.home.arpa`. Alloy is pinned to
v1.21.0, built with Go 1.27.2 and native ML-DSA TLS support. The deployment copies
the controller's system CA bundle from `/etc/ssl/certs/ca-certificates.crt`
to persistent storage and mounts it read-only at the same standard path inside
Alloy; certificate verification remains enabled. Reapply after controller trust
updates to refresh the router's copy.
Suricata still uses its existing enrichment API on port 12345.
The explicit Alloy command enables the stability level required by the existing
write-ahead logs; `CUSTOM_ARGS` is not consumed by the official image. Smoke checks
verify Alloy's HTTP readiness as well as the container state.
Router Loki/Alloy static mappings now use Atlas. FreeIPA and
HCV aliases follow Atlas, and the PXE boot server points at Atlas without
activating iVentoy. Tor NAT rule 20 and IPv4/IPv6 rules 39/40 are removed because
no Tor relay was migrated; I2P forwarding remains active on Atlas.

Use the full `--tags apply` workflow above to install DNS and Alloy configuration,
apply route/rule deletes and sets, restart the containers, and run smoke checks.
Route-only `--tags vyos_config` does not update DNS or Alloy configuration.

Applied on **2026-10-21**, preserving all twelve routes across the current ISP
prefix, retained `2a02:168:6271` prefix and ULA. All **86 router smoke checks**
passed. The official Alloy v1.21.0 image reports Go 1.27.2, whose
[TLS implementation supports ML-DSA](https://go.dev/doc/go1.27).
Verified HTTPS delivery through the deployed pipeline in Loki with
`{job="vyos-syslog",host="router.home.arpa"}`: the fixed acceptance message
arrived at **14:01:15 UTC**, following native TLS probes from Aperture and the
router. The container remained ready with zero restarts and no new warnings.

Replaying the existing WAL produced historical ingress rejections (HTTP 418/400
and rate limiting), with 127,503 dropped entries reported before new delivery
settled. The counter then stayed unchanged while successful sends increased.
No WAL or position files were deleted or manually truncated. The remaining WAL
was preserved before its normal retention cleanup in root-only router archive
`/config/containers/alloy/migration-wal-replay-20261021.tar.gz`; rejected history
is not confirmed complete in Loki and the archive has not been replayed.

### Cerebrum routing

The GPU workload host is `cerebrum.home.arpa`, VM 230, at `10.0.40.135`, MAC
`bc:24:11:4e:c0:23` and ULA `fd6b:d722:1738:28:be24:11ff:fe4e:c023`.
The route templates select `cerebrum_node_ipv6_suffix`, giving next hop
`fe80::be24:11ff:fe4e:c023` on `eth3` for all six container routes, including
the retained ISP prefix.
Both guests derive the same container pools from their first hostname letter;
do not activate those networks on both guests.
See the [completed handover](../docs/archive/migration/cerebrum-cutover.md)
for rollback and the [acceptance record](../docs/archive/migration/cerebrum-migration.md)
for measurements.

### Recovering adguard_users from the router

```bash
ssh vyos@router sudo cat /config/containers/adguard/conf/AdGuardHome.yaml | grep -A5 'users:'
```

This returns the username and bcrypt-hashed password. Add to `secrets.yml` as:

```yaml
adguard_users:
  - name: admin
    password: "$2y$..."
```
