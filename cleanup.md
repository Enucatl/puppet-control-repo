# Repository cleanup plan

Prepared 2026-11-01 from a read-only repository audit. A later session will
implement this plan. Recheck references and host state before each deletion;
file locations describe the audited revision and may move.

## Implementation status (2026-11-01)

Implemented the Beszel catalog ordering fix; retired the old Vault policy and
offline-intermediate paths, unused Docker Python project, duplicate NFS setting,
Cognition configuration, completed router migration commands, historical archive
and dated SSH inventory. Current Atlas authorization tests replace the obsolete
policy fixture. Recovery protections and unresolved operational limits moved into
current runbooks. The supported rehearsal remains, with sandbox-scoped output;
OpenVox versioning no longer includes Puppet Enterprise compatibility.

Live evidence confirmed all 31 router delete targets and Atlas's four obsolete
renewal units absent. The retired health checker, shared credential and Cerebrum
Wolf/vLLM deployment units were also absent; their markers are retired.
Docker publication checks passed: 571 tests passed, 3 skipped. Production was
verified on GitHub, Atlas and the OpenVox receipt at `af5cbad`.

The 14:03 UTC recheck confirmed Codex defaults and regular user configs on all
three intended desktop/Docker targets (Atlas, Aperture and Cerebrum), allowing
retirement of its symlink migration. Hypervisors do not include that class;
their old symlinks are outside this rollout.

Cortex's retained initramfs images were regenerated with
`update-initramfs -u -k all`; its normal hook refreshed both EFI partitions. The exact retired
`root@docker` key material was absent from live authorization files, all four
`/boot` images and all six EFI copies afterward. Allowed Dropbear keys, including
Wolf, remained in every image; kernel selection and live authorization files
were unchanged. Its completed removal marker is retired. No reboot was run.

Conditional items retained:

- Proxmox cache-prune overrides remain to prevent job reinstallation.
- Documented targeted router tasks, Rakefile and manual wake-on-LAN CLI remain;
  operator use has not been ruled out. Optional ansible.cfg trimming was skipped.
- All six live Cognition DNS records were removed with exact-value API calls;
  no Cognition DHCP reservation/lease existed. Installed DNS configuration and
  smoke checks were updated to prevent reintroduction. Cerebrum DNS and Cortex
  DHCP reservations were unchanged; 80 router smoke checks passed.

No VM, disk, snapshot, volume or backup was deleted.

The original plan below records the audit context and conditional decisions.

## Decisions and completed work

- The user confirmed Cognition is completely gone. Its rollback-only DNS,
  enrollment admission, old route cleanup and documentation can be retired.
  Earlier instructions to retain Cognition for rollback are superseded by this
  confirmation. This does not establish that Complex or other recovery assets
  may be deleted.
- `dev` was deleted from GitHub and Atlas's bare control repository. Its local
  tracking ref was pruned. The GitHub tip was already contained in production.
- The user requested merging and deleting `traefik-cloudflare-token-acl`.
  Its commit `9e10f86` adds two Atlas ACL entries for Traefik's Cloudflare token.
  Keep this change: sibling `../traefik` uses the token for current ACME DNS-01
  certificate issuance and renewal. Container UID 1000 maps to host UID 101000.
  The ACLs already existed on disk, and the running container successfully read
  the token without displaying its contents. The merge makes them reproducible.
- Do not implement the other cleanup during this planning session.

## Working rules

Implement the smallest complete change in each group. Preserve unrelated edits.
Search the whole tree, including dynamic filenames, Hiera, tests and CI before
calling a file unused. Update callers, documentation links and explicit CI paths
in the same change as a deletion. Keep tests of current behavior; remove tests
whose only purpose is preserving retired machinery.

Follow AGENTS.md: run tests only in disposable Docker containers with
`docker run --rm --userns=host`, keeping dependencies and artifacts off the host.
Use wake-run for main-thread shell commands expected to exceed 30 seconds.
Use `/opt/puppetlabs/bin/puppet` for Puppet commands.

## 1. Fix the stale Beszel provisioning dependency

`modules/profile/manifests/beszel_agent.pp` has two resource collectors naming
`ipa-user-add-${user}`. No resource creates that title anymore.
`modules/freeipa_users/manifests/provision.pp` creates
`ipa-user-provision-${username}`; Atlas provisions the Beszel account through it.

- Rename both collectors to the current resource title. Preserve ordering of
  account provisioning before account-owned directories. Include the cache
  directory if needed because it also uses the Beszel group.
- Update comments that still describe account creation on the retired Docker
  host.
- Add a meaningful catalog relationship assertion in
  `tests/test_profile_catalogs.py` so fresh provisioning cannot silently lose
  the ordering again. Existing accounts hide this problem.

## 2. Retire the old Docker Vault policy path

Removal candidates, 457 lines before related edits:

- `docker/vault/scripts/08-vault-puppet-policy.sh`
- `tests/test_puppet_pki_vault.py`

The helper configures the retired `docker.home.arpa` compiler and old issuance
roles. Its integration fixture invokes that helper and tests the old policy
migration. Current replacements are `proxmox/atlas-infra.py::configure_access`
and `proxmox/atlas-enroll.py`. `docs/puppet-enrollment.md` explicitly says the old
helper must not replace Atlas policies.

- Remove the obsolete helper and migration fixture. Preserve useful real-Vault
  authorization checks by applying any checks without current equivalents to
  the current Atlas policy/enrollment flow rather than keeping the old helper.
- Remove explicit lint/test paths from `scripts/ci/enrollment.sh` and the old
  exclusion from `scripts/ci/profiles.sh`; update README/runbook references.
- Keep both `docker/vault/scripts/policies/*.hcl`. Production reads them through
  a dynamically constructed filename in `atlas-infra.py`; they are not dead.
- Keep `tests/test_puppet_enrollment_vault.py`, the autosigner and their
  dependencies. This deletion does not make hvac or cryptography unused.

## 3. Retire the historical offline-intermediate experiment

Removal candidates, 299 lines:

- `docker/vault/scripts/offline-intermediate.rb`
- `tests/test_offline_vault_scripts.py`
- `docs/openvox-offline-root.md`

This is a separate historical experiment, not Atlas's current authority path.
References found only documentation, its tests and CI checks.

- Remove explicit Ruby syntax-check input in `scripts/ci/profiles.sh` and the
  test lint/format arguments in `scripts/ci/enrollment.sh`.
- Update incoming links and README's retained-helper table. Move any still
  relevant explanation of current root custody into the current runbook.
- Keep production PKI initialization, intermediate import, self-tests and
  recovery protections.

## 4. Remove proven unused project/configuration files

- Delete `docker/pyproject.toml` and `docker/uv.lock` (114 lines). No current
  build, deployment or CI command selects this old Python >=3.7 Click project;
  current commands select the root or provisioning project. Keep Click where
  current scripts use it.
- Delete `complex_node_ipv6_suffix` and `docker_node_ipv6_suffix` from
  `provisioning/host_vars/router.yml`. Whole-tree searches found only their
  definitions. Do not confuse these with IPv4 variables still used for DNS.
- Delete the second identical `nfs::nfs_v4_idmap_conf: true` entry in
  `data/os/Debian.yaml`.

These items plus sections 2 and 3 account for approximately 870 removable lines,
before CI/documentation edits or replacement authorization coverage.

## 5. Remove Cognition's remaining operational configuration

The user has confirmed Cognition is gone; do not retain configuration solely for
its rollback. Trace all `cognition` and old VM 220 references to distinguish them
from historical prose or unrelated numeric values.

- Remove Cognition-only DNS/DHCP entries and variables from router configuration.
- Remove its exact-certname admission from `proxmox/atlas-enroll.py` and update
  associated tests.
- Remove old-hop route deletion commands once live router inspection confirms
  those routes are absent. Removing a delete command does not remove a stale
  route from the router.
- Remove Cognition-specific smoke checks and tests of rollback retention.
- Rewrite current runbooks to target Cerebrum VM 230 and remove Cognition
  rollback procedures/requirements. Preserve active Cerebrum DNS, routes,
  identity, vLLM image pin, provisioning and controllers.
- Do not run VM, disk, snapshot or volume deletion commands under this code
  cleanup. Cognition is already gone; inventory other assets independently.

## 6. Retire completed host cleanup markers

Check the affected hosts first. A removal marker is still useful until cleanup
has actually applied. Delete related cleanup-only assertions with each marker.

| Candidate | Completion evidence | Connected code/tests |
| --- | --- | --- |
| `modules/profile/manifests/docker_deploy/health_check.pp` | Old `/usr/local/sbin/docker-compose-health-check` absent on Atlas and Cerebrum | Includes and dependencies in `docker_deploy.pp`; `tests/test_profile_catalogs.py`; `docs/06-docker-image-refresh.md` |
| Atlas `atlas-openvox-renew.{service,timer}` and `atlas-transport-renew.{service,timer}` removal entries | All four obsolete units absent/inactive | `data/nodes/atlas.home.arpa.yaml`; catalog cleanup assertions; renewal runbook references |
| Cerebrum `wolf` and `vllm` deployment removal entries | Old deploy path/service units absent | `data/nodes/cerebrum.home.arpa.yaml`; catalog assertions; preserve the actual applications |
| `/run/puppet-ipa-admin-pass` removal | Retired credential absent on affected hosts | `modules/freeipa_users/manifests/init.pp`; catalog assertion |
| Cortex `root-docker` authorized-key removal | Retired authorization absent; retained boot images checked if relevant | `data/nodes/proxmox-cortex.home.arpa.yaml`; SSH inventory |

Do not remove Proxmox `user-toolchain-cache-prune: ensure: absent` overrides:
OS-level Hiera still defines the job, so removing overrides would reinstall it.
Codex symlink migration cleanup was introduced on 2026-11-01; verify rollout
before attempting to remove it.

## 7. Reduce router migration machinery

`provisioning/templates/partials/deletes.j2` currently contains 31 cleanup
commands for old firewall rules, log hostnames, Docker/Aperture/Cognition route
hops, the previous ISP prefix, WireGuard peers and Suricata's old home network.

- Inspect live VyOS configuration and remove each completed delete command.
  Finish outstanding cleanup before removing its enforcement.
- Update `tests/test_router_migration.py` and `tests/test_router_i2p.py` to retain
  checks of current routes, forwarding and log delivery, removing assertions
  that only preserve completed transitions.
- Remove obsolete template imports and variables if the partial becomes empty.
- Review targeted task files `08_i2p.yml`, `09_teamspeak.yml`,
  `10_qbittorrent.yml`, and `11_netflow.yml` (77 lines). They duplicate targeted
  service cutover workflows whose desired configuration lives in the normal
  partials. They are still documented operator commands, so retire them only if
  that workflow is no longer wanted; update router imports, tag instructions
  and tag-selection tests together.

## 8. Reduce historical documentation and frozen acceptance data

`docs/archive/migration/` contains 28 files, 6,185 lines, about 263 KiB. Git
already preserves these plans, investigation transcripts and frozen JSON
results. Most are not runtime inputs.

- Extract any remaining current recovery instructions, retained-asset rules and
  operational limits into current runbooks before deleting redundant history.
- Remove completed plans and acceptance artifacts from the working tree; update
  all incoming links in root/proxmox/provisioning README files and current docs.
- Cognition rollback requirements are superseded by the user's confirmation.
  Complex and other protected recovery assets are not covered by that decision.
- `docs/ssh-key-inventory.json` is a dated investigation, not live configuration.
  It describes keys that current configuration already removes. Retain useful
  unresolved conclusions in current documentation, then delete the stale
  inventory or clearly identify its historical status.
- Do not delete private backup kits, held snapshots or protected archives as a
  side effect of removing repository evidence.

## 9. Decide the reusable rehearsal's future

The OpenVox rehearsal is still documented as supported; it is not proven dead.
It tests a separate offline-root topology rather than Atlas's current topology.

If retaining it, remove obsolete migration-wide readiness output from
`scripts/openvox-rehearsal.py` near `REPORT["blockers"]` and
`ready_for_production=False`. Report what that sandbox run verifies and its
actual limits, without presenting completed production rollout work as pending.
Keep genuine BCJSSE and environment-specific limitations.

If retiring it as completed migration machinery, remove the full connected set:
controller, recovery module, rehearsal tests, sandbox-only Docker/config/fixture
files, docs, CI self-check and cross-test assertions. Main files include
`scripts/openvox-rehearsal.py`, `scripts/openvox_rehearsal_recovery.py`, and
`tests/test_openvox_rehearsal.py` (over 2,000 lines before sandbox files).
Do not delete `docker/openvox` wholesale: production uses `infra-*`,
`transport.rb`, `verify-crypto.rb` and `openssl.cnf`.

## 10. Remove obsolete dependency-management scaffolding

- README tells operators to edit `Puppetfile-without-deps`, which does not exist.
  Replace that instruction with the actual current Puppetfile/r10k workflow.
- `Rakefile` only invokes `ra10ke`; no current CI/deployment caller was found.
  Remove it if no external operator workflow still needs it.
- `scripts/config_version.sh` still supports Puppet Enterprise through
  `config_version-rugged.rb`. This is reachable compatibility code, not an
  unreferenced script. If supporting only the current OpenVox deployment,
  remove that branch and companion helper while preserving current versioning.
- `docker/vault/scripts/wake_on_lan.py` duplicates parts of newer power-control
  paths but remains documented as a manual CLI. Check operator use before
  retiring it; repository caller absence alone is insufficient.
- Optional low-value cleanup: trim `provisioning/ansible.cfg`'s copied example
  comments and obsolete settings without changing active connection behavior.

## Keep unless new evidence proves otherwise

- All local Puppet modules appeared reachable through current Hiera/includes.
- `proxmox/freeipa-client-acceptance.sh`, its cloud-init template and
  `data/roles/freeipa_acceptance.yaml` remain reusable fresh-client checks.
- `atlas-promote.sh`, current provisioning/template builders, the Cerebrum
  launcher and pinned vLLM override are active reproducibility/protection code.
- `docker/vault/scripts/acng.conf` is mounted by current Compose despite its
  odd directory; Vault Kerberos setup and its tests support current SSO.
- Legacy-named `profile::docker_node::*` Samba secret keys still have consumers.
- Hiera role/node symlinks and Proxmox workflow compatibility wrappers are active
  interfaces, not deletion candidates based on naming alone.
- Current authority state, service secrets, bootstrap recovery inputs and
  protected backups are outside the code-deletion scope.

## Verification and publication

Use small reviewable commits by cleanup group. Run focused meaningful checks
after code changes, then the repository's required Docker checks through the
managed pre-push hook. Do not add tests that only mirror trivial deletions.

After publication, verify both GitHub and Atlas production refs and OpenVox's
deployment receipt as documented in `proxmox/README.md`. Git accepting a ref does
not establish deployment success. Confirm the worktree contains no unrelated
changes and summarize exactly which conditional items remain.

Audit limits: no full test suite or live fleet/router inspection was performed
for the cleanup audit. Only the subsequent Traefik investigation inspected its
live configuration, ACLs and token readability; no certificate renewal was run.
