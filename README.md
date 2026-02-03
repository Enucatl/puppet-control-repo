# Puppet Control Repository

A centralized repository for managing infrastructure via Puppet. This repository follows the [Roles and Profiles](https://www.puppet.com/docs/puppet/8/design_and_types/roles_and_profiles_intro.html) pattern and uses `r10k` for deployment.

## Agent Installation & Setup

To enroll a new node:

1. Install the Puppet agent (v8 recommended):
   ```bash
   # Download package from http://apt.puppet.com/
   sudo dpkg -i puppet-release-xxx.deb
   sudo apt update
   sudo apt install puppet-agent
   ```

2. Configure and start the agent:
   ```bash
   sudo /opt/puppetlabs/bin/puppet config set server docker.home.arpa --section main
   sudo /opt/puppetlabs/bin/puppet resource service puppet ensure=running enable=true
   sudo /opt/puppetlabs/bin/puppet ssl bootstrap
   ```

3. Sign the certificate on the Puppet Server:
   ```bash
   sudo puppetserver ca sign --certname <hostname>
   ```

## Deployment

### Managing Dependencies
Manual dependencies are added to `Puppetfile-without-deps`. To resolve and generate the full `Puppetfile`:
```bash
generate-puppetfile -p Puppetfile-without-deps
```

### Manual Deployment
To manually trigger a deployment on the server (though this is usually handled by the git hook):
```bash
sudo -u puppet r10k deploy environment --modules -v info
sudo -u puppet /opt/puppetlabs/puppet/bin/puppet generate types --environment production
```

## Repository Structure

```
.
├── data/                       # Hiera data (Lookups)
│   ├── common.yaml             # Global defaults
│   ├── nodes/                  # Node-specific overrides (docker, pihole, etc.)
│   ├── os/                     # OS-specific settings (Debian, Ubuntu)
│   └── roles/                  # Role-based configurations (desktop, proxmox)
├── manifests/
│   └── site.pp                 # Main entry point (Mapping nodes to roles/profiles)
├── modules/                    # Local/Custom Modules
│   ├── profile/                # Logic layer (The "glue" between data and modules)
│   │   ├── alloy.pp            # Monitoring agent configuration
│   │   └── docker_host.pp      # Docker environment setup
│   ├── freeipa_firefox/        # Firefox configuration for FreeIPA environments
│   ├── freeipa_users/          # Local user management via IPA
│   ├── nfs_config/             # NFS mount bug fixes and config
│   ├── packages/               # Generic package management wrapper
│   ├── postfix_configuration/  # Mail relay settings
│   ├── puppet_configuration/   # Self-management of puppet.conf
│   └── tor_relay/              # Tor relay node configuration
├── provisioning/               # Infrastructure Provisioning
│   ├── router-config.yml       # Ansible playbook for network infrastructure
│   ├── inventory/              # Ansible inventory
│   ├── pyproject.toml / uv.lock # Python environment management (uv)
│   └── templates/              # Jinja2 templates (e.g., AdGuardHome)
├── scripts/                    # Helper Scripts
│   ├── autosign.py             # Policy-based certificate autosigning
│   └── external_node_classifier.py # ENC for environment selection
├── Puppetfile                  # Managed by r10k (Generated)
└── post-receive                # Git hook for automatic server deployment
```

## Key Components

### 1. Hiera (Data)
Data is organized hierarchically. The `data/roles/` directory allows for broad categorization of hardware (e.g., applying `proxmox.yaml` settings to all hypervisors).

### 2. Profiles (`modules/profile`)
This is where the actual configuration logic resides. Instead of putting logic in `site.pp`, create a profile (e.g., `profile::common`) and include it.

### 3. Provisioning
The `provisioning/` directory uses Ansible to handle tasks outside of Puppet's scope, such as initial router configuration and VM bootstrapping. It uses `uv` for high-performance Python dependency management.

### 4. Automation
*   **Autosigning:** The `scripts/autosign.py` script allows for secure, automated certificate signing for new nodes.
*   **Git Hooks:** Pushing to the `production` branch on the server triggers the `post-receive` hook, which runs `r10k` to deploy the code live.
