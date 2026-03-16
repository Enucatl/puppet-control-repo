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

`wake_on_lan.py` automates waking `proxmox-cortex`: sends a WoL packet, unlocks ZFS via Dropbear SSH, then optionally starts a VM (`--vm-id`) or LXC container (`--ct-id`).
