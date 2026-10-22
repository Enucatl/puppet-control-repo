# Puppet Control Repository

Atlas and Aperture replace Docker and Forbearance. Cerebrum (VM 230) is the
XFS replacement for Cognition (VM 220). See the completed
[migration record](docs/archive/migration/cerebrum-migration.md) and
[handover record](docs/archive/migration/cerebrum-cutover.md) for acceptance evidence and rollback.
See [Atlas service configuration](docs/atlas-services.md)
for the current service sources and environment files, and
[Atlas recovery](docs/atlas-recovery.md) for established-authority restoration.

A monorepo for home lab infrastructure: Puppet profiles and Hiera data, Atlas's
container infrastructure, Proxmox provisioning, and router configuration.

Use these current operating instructions:

- [VM provisioning and production publication](proxmox/README.md)
- [OpenVox, Vault and FreeIPA startup](docs/openvox-infra.md)
- [Native OpenVox clients](docs/openvox-native-clients.md) and [enrollment](docs/puppet-enrollment.md)
- [Atlas service configuration](docs/atlas-services.md) and [backup/recovery](docs/atlas-recovery.md)
- [Disposable OpenVox rehearsal](docs/openvox-rehearsal.md) and [Vault limits](docs/openvox-vault-limitations.md)

The completed migration's plans, cutover reports and acceptance evidence are in
the [historical archive](docs/archive/migration/README.md).

## Repository Structure

```
.
├── data/                        # Hiera data (Puppet lookups)
│   ├── common.yaml              # Global defaults
│   ├── nodes/                   # Per-node overrides (atlas.home.arpa.yaml, proxmox.home.arpa.yaml, ...)
│   ├── os/                      # OS-specific settings (Debian, Ubuntu)
│   └── roles/                   # Role-based settings (desktop, proxmox)
├── manifests/
│   └── site.pp                  # Main Puppet entry point
├── modules/                     # Local Puppet modules
│   ├── profile/                 # Profiles: logic layer between Hiera data and modules
│   │   └── manifests/           # alloy, common, docker_host, docker_deploy, dropbear_initramfs, ...
│   ├── packages/                # Generic package management wrapper
│   ├── freeipa_users/           # Local user management via IPA
│   └── ...                      # Other custom modules
├── docker/                      # Docker Compose stack (runs ON atlas.home.arpa)
│   ├── docker-compose.yml       # Core services: OpenVox, Vault, APT cache
│   ├── vault/
│   │   ├── config/              # Vault server configuration
│   │   └── scripts/             # Runtime export/unseal and administrative helpers
│   └── openvox/                 # OpenVox image, runtime and r10k configuration
├── proxmox/                     # Scripts that run ON the Proxmox hypervisor
│   ├── configure-pve-backups.sh # Proxmox Backup Server setup
│   ├── desktop.sh               # Launcher for desktop.py
│   ├── desktop.py               # Desktop VM provisioning
│   ├── docker-server.sh         # Docker VM provisioning
│   ├── ubuntu-server-template.sh# Ubuntu cloud-init template creation
│   └── *.sh                     # Other node provisioning helpers
├── provisioning/                # Ansible playbooks for network infrastructure
│   ├── router.yml               # VyOS router configuration playbook
│   ├── inventory/               # Ansible inventory
│   ├── templates/               # Jinja2 templates (VyOS config, cloud-init partials)
│   └── pyproject.toml / uv.lock # Python deps managed via uv
├── scripts/                     # Autosign policy, ENC and runtime helpers
├── compose.yaml                 # Atlas infrastructure Compose entrypoint
└── Puppetfile                   # r10k-managed external module list (generated)
```

## How the Pieces Relate

Atlas runs OpenVox and Vault through the root `compose.yaml`; FreeIPA has its
own entrypoint at `docker/freeipa/atlas-compose.yaml`. OpenVox serves catalogs
at `openvox.home.arpa:8140`, Vault at `https://vault.home.arpa:8200`, and FreeIPA
at `ipa.home.arpa`. Atlas, Aperture, Cerebrum and the Proxmox hosts consume the
shared Puppet profiles and Hiera configuration.

- **`modules/` + `data/`** contain profiles, roles and node settings.
- **`proxmox/`** creates and enrolls guests with explicit trust and credentials.
- **`provisioning/`** configures the VyOS router through Ansible.
- **`scripts/`** supplies the autosign policy, ENC and runtime helpers.

## Desktop VM Provisioning

Follow the [shared prerequisites and current replacement behavior](proxmox/README.md)
first. On the Proxmox host, install `uv` and run the existing command from a
directory containing your `.env`:

```bash
bash /path/to/puppet-control-repo/proxmox/desktop.sh -l a -i 123 -c 8 -m 8000 -d 128G --puppet-server openvox.home.arpa
```

The launcher runs Python with the repository's locked dependencies. The same
options are available with `uv run --frozen python proxmox/desktop.py` from the
repository root. Defaults remain 8 cores, 8000 MB RAM, and a 128G disk; omitting
`-i` asks Proxmox for the next VM ID, and `-l` restricts the dictionary hostname's
first letter. Replacing an existing VM requires `--rebuild`, an explicit ID and
name, and the matching provisioning ownership marker.

Shared defaults still come from `proxmox/config.sh`. The `.env` file overrides
those defaults and must provide `VAULT_ADDR`, `VAULT_TOKEN`, and `LOG_DIR`
(or leave them set in the environment). `VAULT_CACERT` optionally selects the
Vault CA certificate. Use one `KEY=value` assignment per line; quote values
containing spaces or `#`. Values in `.env` are literal, with no shell expansion.

Desktop cloud-init is built as structured data and written as JSON, which is
valid YAML, after the `#cloud-config` header. It embeds `configure-puppet.sh`
directly and passes enrollment values as command arguments. The temporary
snippet is restricted to its owner and removed when provisioning exits.

## OpenVox server configuration

The container's runtime configuration is versioned under `docker/openvox/`.
[infra-runtime.conf](docker/openvox/infra-runtime.conf) configures one JRuby
worker and recycles it after 10,000 HTTP requests. The infrastructure Compose
configuration supplies the image, mounts, environment and healthcheck.
Use the [authority runbook](docs/openvox-infra.md) for startup and
[recovery runbook](docs/atlas-recovery.md) to restore established state.

## Puppet Agent Setup (New Node)

Use `proxmox/install-openvox.sh` for the accepted signed native package and
`proxmox/configure-puppet.sh` for certname-bound, single-use Vault enrollment.
The installer supports Ubuntu 24.04/26.04 and Debian 13 on amd64, verifies the
archive fingerprint and OS preferences, and leaves Puppet masked. It configures
`key_type=mldsa44`, TLS 1.3, hybrid key exchange and seed-only private-key writes.

Supply authenticated CA trust before enrollment. Guest provisioning requires
`OPENVOX_CA_CERT` to name the operator-verified public CA bundle; it embeds that
bundle in the guest's `/etc/puppetlabs/puppet/ssl/certs/ca.pem`. Existing native
hosts use `proxmox/enroll-proxmox.sh` with the same input. It refuses to replace
different existing trust, and enrollment refuses existing non-ML-DSA identities.
Existing identities require an explicit migration; do not delete them automatically.
See the [native client instructions](docs/openvox-native-clients.md).

## Deployment

From Atlas's `production` checkout, `git push origin` publishes to GitHub and
`/opt/git/openvox/control.git`. The receiver's managed hook deploys through the
OpenVox container, runs r10k, generates Puppet types and refreshes environment
caches. Verify both target refs and the deployment receipt using the
[publication instructions](proxmox/README.md#ordinary-publication); Git may accept
a push even when deployment fails.

### Managing External Module Dependencies

Direct dependencies go in `Puppetfile-without-deps`. To resolve and regenerate the full `Puppetfile`:
```bash
generate-puppetfile -p Puppetfile-without-deps
```

## Router Observability

Router observability is split between VyOS, the Atlas host, and Loki.

- VyOS runs local Alloy as the edge collector.
- DHCP, DNS, and IPv6 NDP identity streams are shipped directly from VyOS Alloy to Loki.
- Suricata remains limited to IoT and Guest VLANs.
- Suricata EVE is tailed by VyOS Alloy, sent to the central Alloy receiver on `atlas.home.arpa`, enriched there, and then written once to Loki.
- VyOS exports IPFIX sampled at one in ten packets from `eth1.20` and `eth1.30` only to `atlas.home.arpa`.
- GoFlow2 runs from [docker/docker-compose.yml](/opt/docker/puppet-control-repo/docker/docker-compose.yml), receives IPFIX on UDP/2055, and writes decoded flow JSON to stdout. It does not perform GeoIP enrichment.
- Atlas Alloy scrapes GoFlow2 container logs, enriches IPFIX records, and writes them to Loki.

Stable Loki jobs:

- `job="dnsmasq"`
- `job="adguard"`
- `job="vyos-ndp"`
- `job="suricata"`
- `job="ipfix"`

### Identity Logs

`dnsmasq` DHCP logging and AdGuard query logging remain collected on the router and shipped directly to Loki.

IPv6 neighbor discovery is captured by a VyOS task that runs `/config/scripts/vyos-ndp-snapshot.sh`. The script writes newline-delimited JSON to `/var/log/vyos-ndp/vyos-ndp.jsonl`; Alloy tails this as line-oriented input. The NDP log has a persistent logrotate config under `/config/logrotate.d`.

Useful verification:

```bash
sudo /config/scripts/vyos-ndp-snapshot.sh
wc -l /var/log/vyos-ndp/vyos-ndp.jsonl
tail -n 2 /var/log/vyos-ndp/vyos-ndp.jsonl
```

Then query Loki:

```logql
{job="vyos-ndp"}
```

### GeoIP Enrichment

GeoIP enrichment is centralized on `atlas.home.arpa`.

- `geoipupdate` is installed on the Atlas host, not in a container.
- MaxMind credentials come from the existing Vault-backed Puppet values:
  - `profile::alloy::maxmind_account_id`
  - `profile::alloy::maxmind_license_key`
- If either secret is missing, Puppet suppresses the GeoIP updater and the central Alloy enrichment config.
- The MaxMind City database is stored under `/var/lib/geoip`.
- Puppet runs one initial `geoipupdate` before Alloy restarts and keeps the database fresh with a weekly systemd timer.

Alloy configuration is assembled from base, Docker, and optional router-enrichment
templates. Hiera enables those layers and supplies host-specific values. See
[the profile module](modules/profile/README.md) for ownership and catalog regression
tests.

The enrichment boundary is Alloy, not GoFlow2. Country codes are Loki labels because they are low-cardinality and useful for filtering. City-level fields are Loki structured metadata, not labels and not JSON-body rewrites. That keeps the original Suricata and IPFIX JSON bodies intact while exposing city, continent, latitude, longitude, postal code, timezone, and subdivision fields in Grafana and LogQL result fields.

GeoIP lookup skips private, multicast, loopback, link-local, documentation, ULA, and the locally delegated IPv6 prefix.

Useful query patterns:

```logql
{job="ipfix", dest_country="NL"} | dest_geoip_city_name="Amersfoort"
```

```logql
{job="suricata"} | json | event_type="alert"
```

Avoid broad metadata filters without an indexed stream selector. Start with labels such as `job`, `host`, `src_country`, or `dest_country`, then filter on structured metadata.

### IPFIX Volume Checks

IPFIX sampling is set to one in ten packets on `eth1.20` and `eth1.30`; exported flow statistics are estimates.

Measure event rate in Loki:

```logql
sum(rate({job="ipfix"}[5m]))
```

Measure daily count:

```logql
sum(count_over_time({job="ipfix"}[24h]))
```

Measure Loki disk growth on `atlas.home.arpa`:

```bash
docker volume inspect grafana-loki_loki_data
sudo du -sh /var/lib/docker/100000.100000/volumes/grafana-loki_loki_data/_data
```

Check again roughly 24 hours later. That delta is the useful signal for whether the sampled IPFIX volume is sustainable with current retention.

During larger transfers or speed tests, watch:

- VyOS CPU and interface drops.
- GoFlow2 CPU and memory.
- Atlas Alloy CPU and memory.
- Loki ingest, disk growth, and query responsiveness.

Adjust the IPFIX sampling rate or monitored interfaces if the 24-hour volume, disk growth, query latency, or router/resource metrics justify it.

### Relevant Files

- [data/nodes/atlas.home.arpa.yaml](/opt/docker/puppet-control-repo/data/nodes/atlas.home.arpa.yaml)
- [docker/docker-compose.yml](/opt/docker/puppet-control-repo/docker/docker-compose.yml)
- [modules/profile/manifests/alloy.pp](/opt/docker/puppet-control-repo/modules/profile/manifests/alloy.pp)
- [modules/profile/manifests/alloy/geoip.pp](/opt/docker/puppet-control-repo/modules/profile/manifests/alloy/geoip.pp)
- [modules/profile/templates/GeoIP.conf.epp](/opt/docker/puppet-control-repo/modules/profile/templates/GeoIP.conf.epp)
- [modules/profile/templates/alloy.config.epp](/opt/docker/puppet-control-repo/modules/profile/templates/alloy.config.epp)
- [modules/profile/templates/alloy/router.config.epp](/opt/docker/puppet-control-repo/modules/profile/templates/alloy/router.config.epp)
- [provisioning/templates/partials/system.j2](/opt/docker/puppet-control-repo/provisioning/templates/partials/system.j2)
- [provisioning/templates/app_configs/alloy-vyos.alloy.j2](/opt/docker/puppet-control-repo/provisioning/templates/app_configs/alloy-vyos.alloy.j2)
- [provisioning/templates/app_configs/vyos-ndp-snapshot.sh.j2](/opt/docker/puppet-control-repo/provisioning/templates/app_configs/vyos-ndp-snapshot.sh.j2)
- [provisioning/templates/app_configs/vyos-ndp-logrotate.j2](/opt/docker/puppet-control-repo/provisioning/templates/app_configs/vyos-ndp-logrotate.j2)

## Vault lifecycle and administration

The root `compose.yaml` selects the production Vault and OpenVox definitions in
`docker/docker-compose.yml`. Vault uses its pinned image, TLS configuration and
read-only `docker/vault/secrets/` certificate directory. The unsealer mounts only
its existing key share and public TLS-key pin. Ordinary startup is:

```sh
docker compose up -d --wait vault vault-unsealer openvox
```

Fresh authority creation remains an explicit one-time operation:
`sudo python3 proxmox/atlas-infra.py initialize`. Restore established authority
state on recovery; do not repeat initialization. See the
[authority runbook](docs/openvox-infra.md) and [recovery notes](docs/atlas-recovery.md).
The old HTTP bootstrap, numbered root/intermediate setup and legacy FreeIPA CSR
signing services have been removed.

Bootstrap writes certificate PEMs, Vault recovery JSON/root token and IPA
passwords beside each service's Compose file, under gitignored `secrets/`.
Puppet renews the Vault and IPA certificate PEMs. Public `ca.pem` files contain
CA certificates, not private signing keys. The root signing key stays in Vault.

Retained helpers in `docker/vault/scripts/` have separate purposes:

| Script | Purpose |
| --- | --- |
| `unseal.sh` | Existing automatic unseal and healthcheck |
| `backup.sh` | Existing weekly consistent Vault export for Backrest |
| `08-vault-puppet-policy.sh` | Legacy Docker authority policy helper; Atlas uses `atlas-infra.py` and `atlas-enroll.py` |
| `10-vault-ldap.sh` | Manual LDAP auth configuration |
| `13-vault-admin-policy.sh` | Manual administrator policy and LDAP group mapping |
| `99-revoke-root-token.sh` | Explicit root-token revocation after independent administrator access is verified |
| `issue-device-cert.sh` | Manual device certificate/PKCS#12 export |
| `offline-intermediate.rb` | Separate historical offline-root experiment; not Atlas startup |

The weekly `vault-snapshot.timer` still runs Monday at 01:00 UTC. Its existing
export and Backrest handling remain unchanged. Recovery inputs must also be
retained independently of the live Vault host.

Root-token revocation remains an explicit administrator action. Supply an
independent administrator `VAULT_TOKEN` and set `ROOT_TOKEN_FILE` to the protected
bootstrap token. The helper verifies independent access, revokes the bootstrap
token and removes that local token file. Review its child tokens/leases first;
revocation can revoke those too.

Puppet bootstrap tokens are single-use and bound to one exact certname. See
[Puppet enrollment](docs/puppet-enrollment.md) for rollout and replacement-token
instructions. Certificate logins retain their existing runtime policy.

Wolf-specific bootstrap scripts:

- [docker/wolf/scripts/README.md](/opt/docker/puppet-control-repo/docker/wolf/scripts/README.md)
- [docker/wolf/scripts/10-proxmox-token.py](/opt/docker/puppet-control-repo/docker/wolf/scripts/10-proxmox-token.py)
- [docker/wolf/scripts/20-vault-wolf.sh](/opt/docker/puppet-control-repo/docker/wolf/scripts/20-vault-wolf.sh)
- [docker/wolf/scripts/30-create-wolf-operator.sh](/opt/docker/puppet-control-repo/docker/wolf/scripts/30-create-wolf-operator.sh)

`wake_on_lan.py` automates waking `proxmox-cortex`: sends a WoL packet, unlocks ZFS via Dropbear SSH, then optionally starts a VM (`--vm-id`) or LXC container (`--ct-id`). The `wolf` compose service uses the same Dropbear unlock boundary, but its Proxmox lifecycle control now goes through the Proxmox API token stored in Vault.

## Security baseline

This compose project uses the shared [docker-compose-security-baseline](https://github.com/Enucatl/docker-compose-security-baseline) for common container hardening defaults, including capabilities, no-new-privileges, memory/swap, and PID limits.
