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
│   ├── docker-compose.yml       # Core services: Vault, FreeIPA, APT cache
│   ├── vault/
│   │   ├── config/              # Vault server configuration
│   │   └── scripts/             # Bootstrap scripts (01-13) + wake_on_lan.py
│   └── puppet/
│       └── config/              # r10k configuration and empty puppet.conf placeholder
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
├── scripts/                     # Puppet Server helper scripts
│   ├── autosign.py              # Policy-based certificate autosigning
│   ├── configure-puppetserver.sh # Host Puppet Server JRuby tuning
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
│  └── APT cache   ← package mirror for all nodes         │
│                                                         │
│  puppetserver.service (host systemd service)             │
│  └── Puppet      ← reads THIS repo via r10k               │
└────────────────┬────────────────────────────────────────┘
                 │ manages (puppet agent)
     ┌───────────┼───────────────┐
     ▼           ▼               ▼
  docker      proxmox-cortex   other nodes
  (self)      pihole, ...
```

- **Puppet Server** runs as the host systemd service `puppetserver.service` on `docker.home.arpa` and serves catalogs to all managed nodes, including that host. **`docker/`** contains the accompanying container services.
- **`modules/` + `data/`** are the Puppet content — profiles, roles, and Hiera data consumed by every node.
- **`proxmox/`** contains provisioning scripts for new VMs/LXC containers on the hypervisor. They are not managed by Puppet; they run manually or via cron.
- **`provisioning/`** handles infrastructure that Puppet cannot reach at boot time — primarily router/VyOS configuration via Ansible.
- **`scripts/`** are server-side Puppet helpers (autosign policy, ENC) deployed alongside the Puppet Server.

## Desktop VM Provisioning

On the Proxmox host, install `uv` and run the existing command from a directory
containing your `.env`:

```bash
bash /path/to/puppet-control-repo/proxmox/desktop.sh -l a -i 123 -c 8 -m 8000 -d 128G
```

The launcher runs Python with the repository's locked dependencies. The same
options are available with `uv run --frozen python proxmox/desktop.py` from the
repository root. Defaults remain 8 cores, 8000 MB RAM, and a 128G disk; omitting
`-i` asks Proxmox for the next VM ID, and `-l` restricts the dictionary hostname's
first letter. An existing VM at the selected ID is replaced.

Shared defaults still come from `proxmox/config.sh`. The `.env` file overrides
those defaults and must provide `VAULT_ADDR`, `VAULT_TOKEN`, and `LOG_DIR`
(or leave them set in the environment). `VAULT_CACERT` optionally selects the
Vault CA certificate. Use one `KEY=value` assignment per line; quote values
containing spaces or `#`. Values in `.env` are literal, with no shell expansion.

Desktop cloud-init is built as structured data and written as JSON, which is
valid YAML, after the `#cloud-config` header. It embeds `configure-puppet.sh`
directly and passes enrollment values as command arguments. The temporary
snippet is restricted to its owner and removed when provisioning exits.

## Puppet Server Host Configuration

The server is installed directly on `docker.home.arpa`; the current bootstrap
scripts assume it is already installed. Its runtime configuration lives in
`/etc/puppetlabs/puppetserver/conf.d/`, and JVM settings are in
`/etc/default/puppetserver`.

To configure one JRuby worker and recycle it after 10,000 handled HTTP requests,
run from this repository on the server host:

```bash
bash scripts/configure-puppetserver.sh
sudo systemctl restart puppetserver
```

The script preserves a first-run backup beside `puppetserver.conf` as
`puppetserver.conf.before-homelab-tuning` and replaces its own tuning block on
reruns. It preserves the existing 1 GiB heap (`-Xms1024m -Xmx1024m`) and other
configuration. Restarting applies the changes and briefly interrupts service.
One worker serializes compilations; recycling reloads Puppet code and can delay
queued requests. The threshold counts requests, not agent runs or elapsed time.
This script is run manually; pushing the repo does not apply these settings.

Other explicit settings observed in the host's `puppet.conf`:

| Setting | Purpose |
|---------|---------|
| `server = docker.home.arpa` | Agent's primary server |
| `certificate_revocation = leaf` in `[main]` | Check leaf certificate revocation rather than the whole chain |
| `autosign = /etc/puppetlabs/code/environments/production/scripts/autosign.py` | Repository autosigning policy |
| `node_terminus = exec` and `external_nodes = /etc/puppetlabs/code/environments/production/scripts/external_node_classifier.py` | Repository ENC selects `dev` for `.dev.home.arpa` nodes, otherwise `production` |
| `number_of_facts_soft_limit = 10000` in `[agent]` | Raised fact-count warning threshold |

The server's data/log/run/code paths are explicitly set to the usual package
locations. The installed systemd unit has no visible local drop-ins. The protected
`conf.d` files could not be read during this inspection, so this is not a complete
inventory of server overrides or confirmation of its current worker count.

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
- VyOS exports IPFIX sampled at one in ten packets from `eth1.20` and `eth1.30` only to `docker.home.arpa`.
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

Measure Loki disk growth on `docker.home.arpa`:

```bash
docker volume inspect grafana-loki_loki_data
sudo du -sh /var/lib/docker/100000.100000/volumes/grafana-loki_loki_data/_data
```

Check again roughly 24 hours later. That delta is the useful signal for whether the sampled IPFIX volume is sustainable with current retention.

During larger transfers or speed tests, watch:

- VyOS CPU and interface drops.
- GoFlow2 CPU and memory.
- Docker-host Alloy CPU and memory.
- Loki ingest, disk growth, and query responsiveness.

Adjust the IPFIX sampling rate or monitored interfaces if the 24-hour volume, disk growth, query latency, or router/resource metrics justify it.

### Relevant Files

- [data/nodes/docker.home.arpa.yaml](/opt/docker/puppet-control-repo/data/nodes/docker.home.arpa.yaml)
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

## Key Vault Bootstrap Scripts (`docker/vault/scripts/`)

Numbered scripts run once to set up Vault and surrounding infrastructure:

| Script | Purpose |
|--------|---------|
| `00-vault-init.sh` | One-off initialization; save recovery output and split runtime credentials |
| `01-pki-core-setup.sh` | Root CA, Vault TLS cert |
| `02-pki-intermediate.sh` | Intermediate CA |
| `03-puppet-external-ca.sh` | Puppet external CA config |
| `04-sign-csr.sh` | Sign FreeIPA CSR |
| `05-clone-puppet-repo.sh` | Clone this repo onto the server |
| `06-vault-puppet.sh` | Cert auth + KV v2 for Puppet |
| `07-configure-sudo.sh` | FreeIPA sudo rules |
| `08-vault-puppet-policy.sh` | Puppet runtime and single-use enrollment policies |
| `10-vault-ldap.sh` | LDAP auth backend |
| `11-vault-airflow.sh` | Airflow KV policy |
| `13-vault-admin-policy.sh` | Admin policy + LDAP group mapping |
| `99-revoke-root-token.sh` | Final step after all setup and an independent admin login have been tested |

Vault credentials use three separate volumes: `vault_recovery` holds the complete
initialization response, `vault_bootstrap` holds only the temporary root token,
and `vault_unseal` holds only the unseal share. Directories are mode `0700` and
credential files `0600`, owned by container UID/GID `100:100`. Only the one-off
initializer mounts all three. Setup containers mount the bootstrap token
read-only; the running unsealer mounts only the unseal share read-only and the
host's public CA bundle. The Vault server mounts none of these volumes.

For a new Vault, run `docker/vault/bootstrap.sh` once from the repository root.
It builds the unsealer and setup images, selects the HTTP listener using
`docker/vault/bootstrap.compose.yml`, initializes and unseals Vault, and generates
the root CA and server certificate. The temporary HTTP port is published only on
host loopback. The script stops Vault and the unsealer after generating the
certificates. The initializer never prints credentials or overwrites an existing
recovery file. An already initialized server with missing recovery output requires
restoring the saved credentials, not reinitializing.

Install the generated public CA in the Docker host's trust store, then start the
normal HTTPS configuration. On this Debian/Ubuntu host, run from the repository
root:

```bash
vault_ca=$(mktemp)
docker compose -f docker/docker-compose.yml run --rm --no-deps \
  --entrypoint cat vault-pki-core-setup /certificates/ca.crt > "$vault_ca"
sudo install -m 0644 "$vault_ca" /usr/local/share/ca-certificates/vault-ca.crt
sudo update-ca-certificates
rm "$vault_ca"
export VAULT_ADDR=https://hcv.home.arpa:8200
export VAULT_CACERT=/etc/ssl/certs/ca-certificates.crt
docker compose -f docker/docker-compose.yml up -d --wait --wait-timeout 180 vault vault-unsealer
```

Persist these HTTPS environment values in the deployment environment. Continue
with the intermediate CA and FreeIPA signing setup jobs using the normal Compose
file. Setup jobs use a built image with `jq` already installed and a writable
temporary filesystem; they do not install packages at runtime.

Back up the unseal share outside this host, independently of Vault, and verify a
restore with a Vault data backup. The recovery volume is not an off-host backup.
Never keep recovery material in the shared `certificates` volume.

After every setup step (including optional Wolf setup), log in using the LDAP
admin account and test it. Run `99-revoke-root-token.sh` on the host with
`ROOT_TOKEN_FILE` pointing to a private local copy of the bootstrap token.
The script checks independent admin access and removes that local copy after
successful revocation. Remove the token from the bootstrap volume afterward.
Normal root revocation also revokes its child tokens and leases: review these
first. Setup reruns must use an administrator `VAULT_TOKEN` once root is revoked.
Emergency root generation requires the unseal-key quorum; ordinary admin access
does not replace possession of those shares.

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
