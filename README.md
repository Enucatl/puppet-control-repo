# Puppet Control Repository

A monorepo for home lab infrastructure. It combines Puppet configuration management with the supporting infrastructure that runs the Puppet server itself — plus provisioning tools and Proxmox scripts — all in one place.

## Repository Structure

```
.
├── data/                        # Hiera data (Puppet lookups)
│   ├── common.yaml              # Global defaults
│   ├── nodes/                   # Per-node overrides (docker.yaml, proxmox.yaml, ...)
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
├── docker/                      # Docker Compose stack (runs ON docker.home.arpa)
│   ├── docker-compose.yml       # Core services: Vault, FreeIPA, Puppet Server, APT cache
│   ├── vault/
│   │   ├── config/              # Vault server configuration
│   │   └── scripts/             # Bootstrap scripts (01-13) + wake_on_lan.py
│   └── puppet/
│       └── config/              # Puppet Server configuration
├── proxmox/                     # Scripts that run ON the Proxmox hypervisor
│   ├── configure-pve-backups.sh # Proxmox Backup Server setup
│   ├── desktop.sh               # Desktop VM provisioning
│   ├── docker-server.sh         # Docker VM provisioning
│   ├── ubuntu-server-template.sh# Ubuntu cloud-init template creation
│   └── *.sh                     # Other node provisioning helpers
├── provisioning/                # Ansible playbooks for network infrastructure
│   ├── router.yml               # VyOS router configuration playbook
│   ├── inventory/               # Ansible inventory
│   ├── templates/               # Jinja2 templates (VyOS config, cloud-init partials)
│   └── pyproject.toml / uv.lock # Python deps managed via uv
├── scripts/                     # Puppet Server helper scripts
│   ├── autosign.py              # Policy-based certificate autosigning
│   └── external_node_classifier.py # ENC for environment selection
├── Puppetfile                   # r10k-managed external module list (generated)
└── post-receive                 # Git hook: triggers r10k deploy on push
```

## How the Pieces Relate

```
┌─────────────────────────────────────────────────────────┐
│                  docker.home.arpa (VM 200)               │
│                                                         │
│  docker/docker-compose.yml                              │
│  ├── Vault       ← PKI, secrets, cert auth              │
│  ├── FreeIPA     ← LDAP / Kerberos                      │
│  ├── Puppet      ← reads THIS repo via r10k             │
│  └── APT cache   ← package mirror for all nodes         │
└────────────────┬────────────────────────────────────────┘
                 │ manages (puppet agent)
     ┌───────────┼───────────────┐
     ▼           ▼               ▼
  docker      proxmox-cortex   other nodes
  (self)      pihole, ...
```

- **`docker/`** is the infrastructure that hosts Puppet itself. The Puppet Server runs as a container and serves the catalog to all managed nodes, including `docker.home.arpa` itself.
- **`modules/` + `data/`** are the Puppet content — profiles, roles, and Hiera data consumed by every node.
- **`proxmox/`** contains one-shot shell scripts for provisioning new VMs/LXC containers on the hypervisor. They are not managed by Puppet; they run manually or via cron.
- **`provisioning/`** handles infrastructure that Puppet cannot reach at boot time — primarily router/VyOS configuration via Ansible.
- **`scripts/`** are server-side Puppet helpers (autosign policy, ENC) deployed alongside the Puppet Server.

## Puppet Agent Setup (New Node)

1. Install the agent:
   ```bash
   sudo dpkg -i puppet-release-xxx.deb
   sudo apt update && sudo apt install puppet-agent
   ```

2. Configure and bootstrap:
   ```bash
   sudo /opt/puppetlabs/bin/puppet config set server docker.home.arpa --section main
   sudo /opt/puppetlabs/bin/puppet ssl bootstrap
   sudo /opt/puppetlabs/bin/puppet resource service puppet ensure=running enable=true
   ```

3. Sign the certificate on the Puppet Server:
   ```bash
   sudo puppetserver ca sign --certname <hostname>
   ```

## Deployment

Pushing to `production` triggers the `post-receive` git hook, which runs `r10k` and regenerates Puppet types automatically. To trigger manually:

```bash
sudo -u puppet r10k deploy environment --modules -v info
sudo -u puppet /opt/puppetlabs/puppet/bin/puppet generate types --environment production
```

### Managing External Module Dependencies

Direct dependencies go in `Puppetfile-without-deps`. To resolve and regenerate the full `Puppetfile`:
```bash
generate-puppetfile -p Puppetfile-without-deps
```

## Router Observability

Router observability is split between VyOS, the Docker host, and Loki.

- VyOS runs local Alloy as the edge collector.
- DHCP, DNS, and IPv6 NDP identity streams are shipped directly from VyOS Alloy to Loki.
- Suricata remains limited to IoT and Guest VLANs.
- Suricata EVE is tailed by VyOS Alloy, sent to the central Alloy receiver on `docker.home.arpa`, enriched there, and then written once to Loki.
- VyOS exports unsampled IPFIX to `docker.home.arpa`.
- GoFlow2 runs from [docker/docker-compose.yml](/opt/docker/puppet-control-repo/docker/docker-compose.yml), receives IPFIX on UDP/2055, and writes decoded flow JSON to stdout. It does not perform GeoIP enrichment.
- Docker-host Alloy scrapes GoFlow2 container logs, enriches IPFIX records, and writes them to Loki.

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

GeoIP enrichment is centralized on `docker.home.arpa`.

- `geoipupdate` is installed on the Docker host, not in a container.
- MaxMind credentials come from the existing Vault-backed Puppet values:
  - `profile::docker_host::maxmind_account_id`
  - `profile::docker_host::maxmind_license_key`
- If either secret is missing, Puppet suppresses the GeoIP updater and the central Alloy enrichment config.
- The MaxMind City database is stored under `/var/lib/geoip`.
- Puppet runs one initial `geoipupdate` before Alloy restarts and keeps the database fresh with a weekly systemd timer.

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

Unsampled IPFIX is currently enabled so real volume can be measured before introducing sampling.

Measure event rate in Loki:

```logql
sum(rate({job="ipfix"}[5m]))
```

Measure daily count:

```logql
sum(count_over_time({job="ipfix"}[24h]))
```

Measure Loki disk growth on `docker.home.arpa`:

```bash
docker volume inspect grafana-loki_loki_data
sudo du -sh /var/lib/docker/100000.100000/volumes/grafana-loki_loki_data/_data
```

Check again roughly 24 hours later. That delta is the useful signal for whether unsampled IPFIX is sustainable with current retention.

During larger transfers or speed tests, watch:

- VyOS CPU and interface drops.
- GoFlow2 CPU and memory.
- Docker-host Alloy CPU and memory.
- Loki ingest, disk growth, and query responsiveness.

Introduce IPFIX sampling only if the 24-hour volume, disk growth, query latency, or router/resource metrics justify it.

### Relevant Files

- [data/nodes/docker.yaml](/opt/docker/puppet-control-repo/data/nodes/docker.yaml)
- [docker/docker-compose.yml](/opt/docker/puppet-control-repo/docker/docker-compose.yml)
- [modules/profile/manifests/docker_host.pp](/opt/docker/puppet-control-repo/modules/profile/manifests/docker_host.pp)
- [modules/profile/templates/GeoIP.conf.epp](/opt/docker/puppet-control-repo/modules/profile/templates/GeoIP.conf.epp)
- [modules/profile/templates/alloy.config.epp](/opt/docker/puppet-control-repo/modules/profile/templates/alloy.config.epp)
- [provisioning/templates/partials/system.j2](/opt/docker/puppet-control-repo/provisioning/templates/partials/system.j2)
- [provisioning/templates/app_configs/alloy-vyos.alloy.j2](/opt/docker/puppet-control-repo/provisioning/templates/app_configs/alloy-vyos.alloy.j2)
- [provisioning/templates/app_configs/vyos-ndp-snapshot.sh.j2](/opt/docker/puppet-control-repo/provisioning/templates/app_configs/vyos-ndp-snapshot.sh.j2)
- [provisioning/templates/app_configs/vyos-ndp-logrotate.j2](/opt/docker/puppet-control-repo/provisioning/templates/app_configs/vyos-ndp-logrotate.j2)

## Key Vault Bootstrap Scripts (`docker/vault/scripts/`)

Numbered scripts run once to set up Vault and surrounding infrastructure:

| Script | Purpose |
|--------|---------|
| `01-pki-core-setup.sh` | Root CA, Vault TLS cert |
| `02-pki-intermediate.sh` | Intermediate CA |
| `03-puppet-external-ca.sh` | Puppet external CA config |
| `04-sign-csr.sh` | Sign FreeIPA CSR |
| `05-clone-puppet-repo.sh` | Clone this repo onto the server |
| `06-vault-puppet.sh` | Cert auth + KV v2 for Puppet |
| `07-configure-sudo.sh` | FreeIPA sudo rules |
| `08-vault-puppet-policy.sh` | Puppet Vault policy |
| `10-vault-ldap.sh` | LDAP auth backend |
| `11-vault-airflow.sh` | Airflow KV policy |
| `13-vault-admin-policy.sh` | Admin policy + LDAP group mapping |

Wolf-specific bootstrap scripts:

- [docker/wolf/scripts/README.md](/opt/docker/puppet-control-repo/docker/wolf/scripts/README.md)
- [docker/wolf/scripts/10-proxmox-token.sh](/opt/docker/puppet-control-repo/docker/wolf/scripts/10-proxmox-token.sh)
- [docker/wolf/scripts/20-vault-wolf.sh](/opt/docker/puppet-control-repo/docker/wolf/scripts/20-vault-wolf.sh)
- [docker/wolf/scripts/30-create-wolf-operator.sh](/opt/docker/puppet-control-repo/docker/wolf/scripts/30-create-wolf-operator.sh)

`wake_on_lan.py` automates waking `proxmox-cortex`: sends a WoL packet, unlocks ZFS via Dropbear SSH, then optionally starts a VM (`--vm-id`) or LXC container (`--ct-id`). The `wolf` compose service uses the same Dropbear unlock boundary, but its Proxmox lifecycle control now goes through the Proxmox API token stored in Vault.

## Security baseline

This compose project uses the shared [docker-compose-security-baseline](https://github.com/Enucatl/docker-compose-security-baseline) for common container hardening defaults, including capabilities, no-new-privileges, memory/swap, and PID limits.
