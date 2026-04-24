# configure the router

## Setup

```bash
uv sync
```

## Testing progression

### 1 — syntax check (local only, no vault/router needed)
```bash
uv run ansible-playbook -i inventory/router.yml router.yml --vault-password-file vars/password --syntax-check
```

### 2 — diff (read-only, safe)
Fetches `show configuration commands` from the router, renders the template locally, and diffs both sides in set-command format.
```bash
uv run ansible-playbook -i inventory/router.yml router.yml --vault-password-file vars/password --tags diff
```

### 3 — dry-run apply (no commit)
Enters configure mode, loads the commands into candidate config, reports what would change, then discards.
```bash
uv run ansible-playbook -i inventory/router.yml router.yml --vault-password-file vars/password --tags apply --check
```

### 4 — apply
```bash
uv run ansible-playbook -i inventory/router.yml router.yml --vault-password-file vars/password --tags apply
```

### 5 — smoke checks
Runs automatically at the end of `--tags apply` and can also be executed on its own.
```bash
uv run ansible-playbook -i inventory/router.yml router.yml --vault-password-file vars/password --tags healthcheck
```

### 6 — rolling upgrade
Downloads the latest signed VyOS rolling ISO, verifies it with minisign, installs it, reboots, then runs the full deployment and smoke checks.
```bash
uv run ansible-playbook -i inventory/router.yml router.yml --vault-password-file vars/password --tags upgrade
```

## Workflow for any change

1. Edit a partial under `templates/partials/`
2. `--tags diff` to review the delta
3. `--tags apply` to push it
4. `--tags diff` again to confirm clean
5. `--tags healthcheck` if you want to re-run router smoke checks on demand
6. `--tags upgrade` when you need to move the router to the latest signed rolling release

## Secrets (vars/secrets.yml)

`vars/secrets.yml` is an Ansible Vault-encrypted file. The vault password is stored in `vars/password` (not committed). To recreate it:

```bash
rm vars/secrets.yml
uv run ansible-vault create vars/secrets.yml --vault-password-file vars/password
```

### Required secrets

`secrets.yml` contains only `adguard_users`. The other secrets (`ipv6_prefix`, `snmp_v3_password`, `wg_server_private_key`, `cloudflare_api_token`) are pulled at runtime from HashiCorp Vault (`vault kv get kv/puppet`).

The rolling upgrade workflow uses the official nightly-builds page and verifies images with minisign before installation.

### Recovering adguard_users from the router

```bash
ssh vyos@router sudo cat /config/containers/adguard/conf/AdGuardHome.yaml | grep -A5 'users:'
```

This returns the username and bcrypt-hashed password. Add to `secrets.yml` as:

```yaml
adguard_users:
  - name: admin
    password: "$2y$..."
```

## Manual deletes

Add `delete ...` lines to `templates/partials/deletes.j2` for stale nodes that the set-command partials won't naturally overwrite (renamed peers, retired rules, removed features). Remove the line once confirmed absent on the router.
