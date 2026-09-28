# Infrastructure security and simplification review

Reviewed on 2026-09-28.

The biggest issue is **excessive trust between desktops, Puppet, Vault, and the hypervisors**. Several credentials intended for routine automation can expose much more of the infrastructure.

The review examined repository configuration and used isolated checks without changing infrastructure or operating live services. These findings describe configured behavior; they do not establish that a compromise occurred.

## Security findings

The issues below are ordered by priority.

1. **High — Vault initialization exposes recovery credentials in logs.**

   [unseal.sh, line 42](docker/vault/scripts/unseal.sh) sends the initialization response—including the root token and sole unseal share—through `tee`. Docker log collection can forward it to Loki. Recovery material also shares a volume with the running Vault server. Remove credential output, separate recovery storage, and revoke the bootstrap root token after setup. If this initialization path has run, check retained logs and rotate exposed credentials.

2. **High — Every Puppet agent receives the FreeIPA administrator password.**

   [freeipa_users, line 2](modules/freeipa_users/manifests/init.pp) requires the password and is included globally. An isolated desktop catalog contained it even with no users to create. The comment claiming tmpfs keeps it out of catalogs is incorrect: Puppet’s [`Sensitive` type redacts logs, not catalogs](https://help.puppet.com/core/current/Content/PuppetCore/lang_data_sensitive.htm). Separate identity provisioning from local group membership and restrict the credential-bearing class to one provisioning host, preferably using a delegated account.

3. **High — Ordinary node certificates unlock shared secrets and other service identities.**

   The [Puppet Vault policy, line 13](docker/vault/scripts/08-vault-puppet-policy.sh) grants access to all of `kv/puppet`, `kv/wolf`, and general certificate issuance. The [issuance role, line 67](docker/vault/scripts/02-pki-intermediate.sh) permits arbitrary `home.arpa` subdomains and wildcards. A compromised admitted node can retrieve infrastructure secrets and mint certificates matching other services’ authentication identities. Split secret access and certificate issuance by node/service; keep shared Hiera secrets accessible only where compilation requires them.

4. **High — Enrollment credentials are both interceptable and reusable across identities.**

   [lib.sh, line 28](proxmox/lib.sh) disables TLS verification while transmitting the parent Vault token. Separately, [autosign.py, line 83](scripts/autosign.py) accepts any token carrying the runtime `puppet` policy for any permitted hostname. Remove `--insecure`; use a separate, short-lived enrollment credential bound to the exact certname and enforce single use.

5. **High — Backup clients can delete backups across clusters.**

   [configure-pbs.sh, line 97](proxmox/configure-pbs.sh) grants datastore-wide `DatastoreAdmin`, then distributes the same credential to both clusters. Namespaces therefore provide no credential isolation. Give each cluster a namespace-scoped token with `DatastoreBackup`; perform retention on PBS. These roles and permissions are documented by [Proxmox](https://pbs.proxmox.com/docs/user-management.html).

6. **High — Router configuration previews can disclose secrets.**

   [diff_vyos.yml, line 31](provisioning/tasks/diff_vyos.yml) writes normalized configurations into predictable `/tmp` files without restrictive permissions, then prints their differences. These configurations contain WireGuard private keys and other credentials. Use a private temporary directory, redact secret-bearing lines before display, and guarantee cleanup with `always`.

7. **High — Remote dotfile changes automatically execute as hypervisor root.**

   [dotfiles.pp, line 15](modules/profile/manifests/dotfiles.pp) follows remote HEAD and executes its `rake links` task; the Proxmox role selects `root`. A compromised repository or erroneous update becomes root code execution. Remove automatic root dotfile execution or pin a reviewed commit. Likewise, pin the security-critical modules following branches in [Puppetfile, line 55](Puppetfile).

8. **Medium — Removing router access from source does not revoke it.**

   [04_apply_vyos.yml, line 45](provisioning/tasks/04_apply_vyos.yml) applies additive `set` commands; its delete template is empty. Removing a WireGuard peer, firewall allowance, or port forward can leave the deployed configuration active. Add explicit revocation handling or scoped replacement of owned configuration sections. This follows the [VyOS module’s set/delete semantics](https://docs.ansible.com/projects/ansible/latest/collections/vyos/vyos/vyos_config_module.html); the live router was not inspected for stale rules.

## Architectural and code simplifications

- **Give host shutdown one owner.** [Wolf, line 444](docker/wolf/app/wolf.py), [backup orchestration, line 224](modules/proxmox_workflows/files/chronicle_backup_orchestrator.py), and [vLLM orchestration, line 124](modules/proxmox_workflows/files/vllm_weekly_window.py) independently shut down the same hypervisor with unrelated locks. Wolf can stop it while backups or other workloads are active. The smallest immediate simplification is to remove host shutdown from individual workflows; centralize it only if automatic power saving remains necessary.

- **Replace the custom Compose health checker with native waiting.** The [112-line checker, line 47](modules/profile/files/docker_compose_health_check.py) accepts `Health=starting`, cleanly exited services, and an empty project as successful. These outcomes were reproduced. Use [`docker compose up --wait --wait-timeout …`](https://docs.docker.com/reference/cli/docker/compose/up/) for long-running services, with explicit handling for one-shot setup jobs.

- **Reuse the existing Puppet enrollment script.** The [desktop cloud-init copy, line 22](proxmox/desktop-cloud-init.yml.tmpl) has diverged: escaped variables produce literal credential and node-type placeholders. Dummy rendering reproduced this. Embed the existing [configure-puppet.sh](proxmox/configure-puppet.sh) instead of maintaining separate copies.

- **Make Vault bootstrap an explicit one-time operation.** [Compose, line 55](docker/docker-compose.yml) requires Vault to be initialized before starting the unsealer that initializes it. Setup scripts also invoke `/bin/sh` while sourcing Bash-only configuration. A single explicit bootstrap procedure with consistent shell and tooling assumptions would remove this circular startup dependency.

- **Fix the scheduled Vault backup’s cleanup before relying on it.** [backup.sh, line 25](docker/vault/scripts/backup.sh) freezes the filesystem, but `set -e` exits on archive failure before thawing it. A mocked failure reproduced the missing unfreeze. This script is scheduled in [docker.yaml, line 343](data/nodes/docker.yaml). Install guaranteed cleanup immediately; then simplify toward a consistent backup mechanism that avoids freezing the backing filesystem for an entire archive operation.

## Verification

**17 catalog tests passed**, Puppet parser validation and Proxmox shell syntax checks passed. Wolf tests produced **20 passes and one failure** because the Vault request bypasses the existing test mock. [CI, line 48](.github/workflows/puppet.yaml) currently omits Wolf tests and Docker source triggers.

The clearest initial code reduction is roughly **150 lines**, principally the custom health checker and duplicated enrollment logic, without adding dependencies. This is an estimate, not a measured implementation diff.
