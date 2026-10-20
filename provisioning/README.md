# configure the router

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
`atlas.home.arpa`. Legacy Docker/FreeIPA records retain their existing address. Vault uses
`vault.home.arpa` on Atlas.

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

These records prepare the migration service endpoints: `https://vault.home.arpa:8200`,
direct TLS `openvox.home.arpa:8140` and FreeIPA at `ipa.home.arpa`. Service certificates must contain the
corresponding DNS names; Atlas's native agent identity remains `atlas.home.arpa`.
DNS publication does not initialize or activate Vault/OpenVox/FreeIPA.

Live acceptance passed on **2026-10-19**, using revision
`b72af4ee85df77bb8b7f04c595cd421073e0e25b`: **47 router smoke checks passed**,
including A/AAAA answers for all three names through AdGuard over IPv4/IPv6 and
dnsmasq directly. Separate checks verified both PTR records, the MAC reservation
and the legacy service addresses. IPv6 connectivity to Atlas passed from the
workstation and router. See [sanitized DNS evidence](../docs/atlas-dns-results.json).
The fixed-MAC creation path passed mocked provisioning tests; a live VM rebuild
was not performed for this DNS change.
The `ipa.home.arpa` addition was deployed on **2026-10-19** with **53 router
smoke checks passed**. Separate checks confirmed all four endpoints' A/AAAA
answers over IPv4/IPv6, Atlas's IPv4/IPv6 canonical PTR records, and unchanged
legacy service addresses. See [FreeIPA DNS evidence](../docs/atlas-ipa-dns-results.json).
FreeIPA service activation remains pending.

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
The isolated collector definition is `../docker/goflow2-compose.json`; its
pinned image matches the source collector and it has no persistent state.
Atlas's dedicated Alloy pipeline enriches flow records with GeoIP. Its local
Loki write URL uses the loopback-only published port 3100 because Alloy's Go
TLS client cannot negotiate the ML-DSA ingress; HTTPS reads retain mTLS.

### Cognition staging and IPv6 route handover

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
