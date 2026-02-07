# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is the `provisioning/` subdirectory of a Puppet control repository. It uses **Ansible** to manage network infrastructure that falls outside Puppet's scope, primarily a **VyOS router** (25Gbps Init7 fiber connection). Python dependencies are managed with **uv**.

The parent repo manages hosts via Puppet (roles/profiles pattern, r10k deployment, Hiera data). This subdirectory handles the router's full configuration as a single Ansible playbook.

## Commands

```bash
# Install dependencies
uv sync

# Apply router configuration (requires vault password file at vars/password)
uv run ansible-playbook -i inventory/router.yml router-config.yml --vault-password-file vars/password

# Dry-run (check mode)
uv run ansible-playbook -i inventory/router.yml router-config.yml --vault-password-file vars/password --check --diff

# Edit encrypted secrets
uv run ansible-vault edit vars/secrets.yml --vault-password-file vars/password
```

## Architecture

- **`router-config.yml`** - Single playbook that fully configures the VyOS router. Uses `vyos.vyos.vyos_config` to send VyOS `set` commands in batch. Sections are numbered and ordered: interfaces/offloads, firewall/flowtables, NAT/routing, SNMPv3, containers/services, application-specific port forwarding rules.
- **`inventory/router.yml`** - Inventory with one host (`router` at 10.0.0.1), using `ansible.netcommon.network_cli` connection and `vyos.vyos.vyos` network OS.
- **`vars/secrets.yml`** - Ansible Vault encrypted file containing sensitive variables (WireGuard keys, SNMP passwords, Cloudflare tokens, AdGuard user credentials, IPv6 prefix).
- **`templates/AdGuardHome.yaml.j2`** - Jinja2 template for the AdGuard Home DNS/DHCP config. Rendered locally, then uploaded to the router via `net_put`.

## Key Conventions

- All VyOS config is sent as `set` commands via the `vyos.vyos.vyos_config` module, not as full config files. The playbook batches them in a single task for atomicity.
- Firewall rules use a jump-target pattern: global forward rules jump to named chains (e.g., `LAN-FORWARD`, `WAN-FORWARD6`). Rule numbers are significant - rule 1 is always the flowtable offload rule.
- IPv4 and IPv6 firewall rules are maintained in parallel (matching rule numbers for the same service).
- Port forwarding requires both a NAT destination rule and a corresponding firewall accept rule in the WAN-FORWARD chain.
- The AdGuard container runs directly on the router with host networking, serving as DNS (port 53) and DHCP for the LAN.
- Variables referencing secrets must exist in `vars/secrets.yml` (vault-encrypted). Non-secret variables are defined inline in `router-config.yml` under `vars:`.
