# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Puppet control repository managing home infrastructure using the **Roles and Profiles** pattern. Deployment is via **r10k**. The repo also contains an Ansible-based `provisioning/` subdirectory for network infrastructure (VyOS router) outside Puppet's scope — see `provisioning/CLAUDE.md` for details.

## Deployment

Pushing to the `production` branch on the Puppet server triggers the `post-receive` git hook, which runs r10k and regenerates types automatically. There is also a `dev` branch/environment.

```bash
# Manual deployment (on the Puppet server)
sudo -u puppet r10k deploy environment --modules -v info
sudo -u puppet /opt/puppetlabs/puppet/bin/puppet generate types --environment production
```

## Puppet Validation

```bash
# Validate Puppet manifests
puppet parser validate manifests/site.pp
puppet parser validate modules/profile/manifests/common.pp

# Check Puppetfile for outdated modules
rake r10k:dependencies
```

## Architecture

### How nodes get classified

1. `site.pp` uses `lookup('classes')` to get a list of classes from Hiera, then includes them all. There are no hardcoded node definitions.
2. The **External Node Classifier** (`scripts/external_node_classifier.py`) assigns nodes to `production` or `dev` environment based on whether their certname ends with `.dev.home.arpa`.
3. **Autosigning** (`scripts/autosign.py`) validates new nodes by checking a Vault token embedded in the CSR's challengePassword OID.

### Hiera hierarchy (data lookup order)

1. **HashiCorp Vault** — secrets fetched from `https://hcv.home.arpa:8200` via cert auth
2. **Node-specific** — `data/nodes/<hostname>.yaml`
3. **Role-based** — `data/roles/<node_type>.yaml` (keyed on `facts.node_type`)
4. **OS-specific** — `data/os/<family>/<name>.yaml`, then `data/os/<family>.yaml`
5. **Common** — `data/common.yaml` (global defaults applied to all nodes)

`common.yaml` defines `lookup_options` with merge strategies (e.g., `classes` uses `unique` merge, allowing layers to add classes additively).

### Profiles pattern

All configuration logic lives in `modules/profile/manifests/`. The `profile::common` class handles Vault certificate management, sysctl settings, and cron jobs. Profiles are included via Hiera `classes` arrays — to assign `profile::docker_host` to the docker node, it's listed in `data/nodes/docker.yaml` under `classes:`.

### Custom modules

Local modules in `modules/` are simple wrappers. Each has a narrow purpose (e.g., `packages` manages apt sources/PPAs/package lists, `postfix_configuration` sets up mail relay). Configuration is data-driven through Hiera parameters, not hardcoded.

### Secrets management

Puppet secrets come from HashiCorp Vault via the `vault_secrets` module and Hiera's `vault_hiera_hash` backend. Vault certificates are managed by `vault_cert` resources created in `profile::common`. The Ansible side uses `ansible-vault` for `provisioning/vars/secrets.yml`.

## Key Conventions

- Classes are assigned to nodes via Hiera `classes` arrays, not in `site.pp`
- Module parameters are set in Hiera data files, not passed explicitly in manifests
- The `home.arpa` domain is used throughout for all internal services
- Puppet server runs on the `docker` node, which also hosts Docker containers behind Traefik
- Two git-sourced modules in Puppetfile (`vault_secrets`, `freeipa`) come from the `enucatl` GitHub account
