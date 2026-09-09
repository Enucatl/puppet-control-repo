# Profiles

Profiles connect this site's Hiera configuration to Puppet resources and component
modules. `manifests/site.pp` includes the merged `classes` list. Hiera selects node,
`node_type` role, OS, and common data, with Vault taking priority. No separate Puppet
`role` module is needed.

## Ownership

| Entry point | Owns |
|---|---|
| `profile::common` | Certificate, sysctl, cron, service, SSH-key, systemd, udev, identity, and ACL children |
| `profile::alloy` | Base and Docker logging, optional router enrichment, GeoIP updater, Alloy Docker-group membership |
| `profile::docker_host` | Docker service and project deployment policy; contains `docker::wolf_network` |
| `profile::docker_node` | Named Docker server's Samba accounts, Traefik reload, and Loki volume permissions |
| `profile::docker_deploy` | Per-project push deployment and optional scheduled refresh |
| `profile::chronicle_backup`, `profile::vllm_weekly_window` | Stable Hiera interfaces to `proxmox_workflows` |
| `profile::proxmox_orchestration` | Stable shared-library installation path interface |
| Other focused profiles | Beszel, user tools, dotfiles, PAM/Vault login, mTLS directories, GPU settings, Dropbear, VFIO |

Small `common::*` classes deliberately remain separate: their Hiera keys identify
resource ownership. Keep node-specific values in Hiera, implementation in manifests,
and substantial rendered configuration in EPP templates.

## Alloy layers

Every host gets Loki output, journal collection, and `/var/log` collection from
`templates/alloy.config.epp`. Docker-role data enables three independent settings:

```yaml
profile::alloy::enable_docker: true
profile::alloy::manage_docker_user: true
profile::alloy::manage_geoip: true
```

These preserve the previous Docker-role behavior. `enable_docker` adds container
discovery and processing; `manage_docker_user` grants Alloy membership in `docker`;
`manage_geoip` installs the database updater when both credentials are available.
The updater still runs on Docker-role hosts without a router receiver.

Only `data/nodes/docker.yaml` enables the central router layer:

```yaml
profile::alloy::enable_router_enrichment: true
profile::alloy::router_listen_address: '10.0.0.128'
```

`templates/alloy/router.config.epp` implements the Suricata receiver and GoFlow2
pipeline. Both pipelines render the same `geoip-stage.epp` partial for source and
destination addresses. Pipeline names, labels, structured metadata, stream
exclusions, and original log bodies remain unchanged. `ipv6-prefix` supplies the
local exclusion prefix; an absent prefix preserves the previous sentinel value.

`profile::alloy` resolves MaxMind credentials once and passes them to the internal
`profile::alloy::geoip` class. Missing either credential suppresses the updater and
router configuration, while retaining base and Docker logging. The initial download
remains ordered before `Service[alloy]`. Missing credentials do not purge an already
installed updater; this preserves the previous resource-omission behavior.

MaxMind credentials use `profile::alloy::maxmind_account_id` and
`profile::alloy::maxmind_license_key`. Samba credentials use
`profile::docker_node::printer_smb_password` and
`profile::docker_node::pictures_smb_password`; the two Samba keys are converted to
`Sensitive` through `data/common.yaml`. Beszel fields, `ipv6-prefix`, and `kv/wolf`
remain unchanged.

## Operational contracts

- `common::cron` purges unmanaged cron entries whenever its merged job map is nonempty.
- Certificate updates retain their notifications, including the Docker certificate's
  Traefik reload. Private-key ownership and ACLs are declared in Hiera.
- Identity adjustments run after `freeipa::client`; they retain SSSD restart behavior.
- Docker packages and repositories remain in role data. Wolf networking remains on
  all existing Docker-role hosts, and runs after the Docker service and common setup.
- Deploy and refresh services share `docker_deploy/service.epp`, but keep distinct
  unit names. Refresh bypasses the Git path condition. When scheduled refresh is on,
  both services retain their health check. Command order is pull, build, deploy, check.
- Setting a project's `ensure: absent` removes its declared units. Merely turning off
  `scheduled_refresh` omits refresh resources; it does not remove existing timers.
- User-toolchain sync retains its lock, update checks, npm publication delay, and
  `ignore-scripts` policy. Cache cleanup retains its existing cron schedule and behavior.
- Dropbear/VFIO retain their initramfs notifications. Beszel retains its FreeIPA
  identity, credential permissions, and systemd hardening.
- Keep historical `ensure: absent` resources until host evidence confirms cleanup.

## Verification and rollout

Run from the repository root:

```sh
/opt/puppetlabs/bin/puppet parser validate modules/profile/manifests modules/proxmox_workflows/manifests
rg --files modules/profile/templates -g '*.epp' -0 | xargs -0 /opt/puppetlabs/bin/puppet epp validate
uv run --frozen pytest -q
```

Catalog tests use a temporary Hiera hierarchy, dummy secrets, and representative
Ubuntu/Debian facts for Docker, complex, forbearance, proxmox, and proxmox-cortex.
They compile the real merged class lists and validate dependency graphs, but never
apply resources or call the production Vault backend. Agent root capability is
simulated only for resource validation; the tests do not acquire root privileges.

Local catalog tests default to deployed dependencies under
`/etc/puppetlabs/code/environments/production/modules`. Set `PUPPET_TEST_MODULEPATH`
to another dependency directory when needed. Workflow and rendering tests run in CI
with public Puppet 8.10; full catalog tests require a populated Puppet modulepath and
are intended for the Puppet server checkout or a separately provisioned test image.

For a refactor comparison, set `PROFILE_BASELINE_ROOT` to a clean checkout of the
previous revision. Node and deployment tests additionally compare native resources,
effective ordering through container anchors, and refresh notifications. Only the
Proxmox source URI move and Alloy whitespace are normalized.

Before production rollout, compile with actual node facts in an isolated environment
and inspect no-op results. A fixture comparison cannot establish live drift or prove
that every host has completed migration. Verify Alloy ingestion and database timer,
deployment unit contents, and Proxmox workflow timers after rollout. Do not trigger
backups, power transitions, or container refreshes solely to validate a code move.
