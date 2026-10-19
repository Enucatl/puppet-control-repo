# configure the router

## Setup

```bash
uv sync
```

## Testing progression

### 1 — syntax check (local only, no vault/router needed)
```bash
VAULT_CACERT=/etc/ssl/certs/ca-certificates.crt uv run ansible-playbook -i inventory/router.yml router.yml --vault-password-file vars/password --syntax-check
```

### 2 — diff (read-only, safe)
Fetches `show configuration commands` from the router, renders the template locally, and diffs both sides in set-command format.
```bash
VAULT_CACERT=/etc/ssl/certs/ca-certificates.crt uv run ansible-playbook -i inventory/router.yml router.yml --vault-password-file vars/password --tags diff
```

### 3 — dry-run apply (no commit)
Enters configure mode, loads the commands into candidate config, reports what would change, then discards.
```bash
VAULT_CACERT=/etc/ssl/certs/ca-certificates.crt uv run ansible-playbook -i inventory/router.yml router.yml --vault-password-file vars/password --tags apply --check
```

### 4 — apply
```bash
VAULT_CACERT=/etc/ssl/certs/ca-certificates.crt uv run ansible-playbook -i inventory/router.yml router.yml --vault-password-file vars/password --tags apply
```

For changes that only touch the declarative VyOS config, skip app files,
container image pulls, restarts, and smoke checks:
```bash
VAULT_CACERT=/etc/ssl/certs/ca-certificates.crt uv run ansible-playbook -i inventory/router.yml router.yml --vault-password-file vars/password --tags vyos_config
```

### 5 — smoke checks
Runs automatically at the end of `--tags apply` and can also be executed on its own.
```bash
VAULT_CACERT=/etc/ssl/certs/ca-certificates.crt uv run ansible-playbook -i inventory/router.yml router.yml --vault-password-file vars/password --tags healthcheck
```

### 6 — rolling upgrade
Downloads the latest signed VyOS rolling ISO, verifies it with minisign, installs it, reboots, then runs the full deployment and smoke checks.
```bash
VAULT_CACERT=/etc/ssl/certs/ca-certificates.crt uv run ansible-playbook -i inventory/router.yml router.yml --vault-password-file vars/password --tags upgrade
```

## Workflow for any change

1. Edit a partial under `templates/partials/`
2. `--tags diff` to review the delta
3. `--tags vyos_config` to push router config only, or `--tags apply` for the full workflow
4. `--tags diff` again to confirm clean
5. `--tags healthcheck` if you want to re-run router smoke checks on demand
6. `--tags upgrade` when you need to move the router to the latest signed rolling release

## Atlas infrastructure DNS and DHCP

`host_vars/router.yml` reserves Atlas at `10.0.0.158` for MAC
`bc:24:11:4e:a4:9e`. The Atlas VM creation script keeps that MAC across rebuilds;
update both declarations together if the NIC reservation changes.
The dnsmasq template publishes exact A and AAAA records for `atlas.home.arpa`,
`vault.home.arpa` and `openvox.home.arpa` at this address and Atlas's verified
EUI-64 ULA `fd6b:d722:1738:0:be24:11ff:fe4e:a49e`. The ULA is retained across
ISP prefix changes; the pinned NIC MAC preserves its interface identifier on
rebuild. Reverse DNS remains
`atlas.home.arpa`. Legacy Docker/FreeIPA/`hcv.home.arpa` records retain their
existing address.

Apply only DNS/DHCP and its smoke checks with:

```bash
VAULT_CACERT=/etc/ssl/certs/ca-certificates.crt uv run --frozen ansible-playbook -i inventory/router.yml router.yml --vault-password-file vars/password --tags dns,healthcheck
```

This path validates the staged configuration with the running dnsmasq image,
backs up the prior file to `/config/containers/dnsmasq/dnsmasq.conf.previous`,
installs the configuration and updated smoke script, then restarts dnsmasq and
AdGuard to clear cached DNS answers. It does not apply the declarative router
configuration or update other containers. Full `--tags apply` also includes
these DNS declarations. Smoke checks verify all three names through AdGuard
over IPv4/IPv6 and dnsmasq directly, plus Atlas's canonical PTR record.

These records prepare #13's service endpoints: `https://vault.home.arpa:8200`
and direct TLS `openvox.home.arpa:8140`. Service certificates must contain the
corresponding DNS names; Atlas's native agent identity remains `atlas.home.arpa`.
DNS publication does not initialize or activate Vault/OpenVox.

Live acceptance passed on **2026-10-19**, using revision
`b72af4ee85df77bb8b7f04c595cd421073e0e25b`: **47 router smoke checks passed**,
including A/AAAA answers for all three names through AdGuard over IPv4/IPv6 and
dnsmasq directly. Separate checks verified both PTR records, the MAC reservation
and the legacy service addresses. IPv6 connectivity to Atlas passed from the
workstation and router. See [sanitized DNS evidence](../docs/atlas-dns-results.json).
The fixed-MAC creation path passed mocked provisioning tests; a live VM rebuild
was not performed for this DNS change.

## Secrets (vars/secrets.yml)

`vars/secrets.yml` is an Ansible Vault-encrypted file. The vault password is stored in `vars/password` (not committed). To recreate it:

```bash
rm vars/secrets.yml
uv run ansible-vault create vars/secrets.yml --vault-password-file vars/password
```

## Manual deletes

Add `delete ...` lines to `templates/partials/deletes.j2` for stale nodes that the set-command partials won't naturally overwrite. Remove the line once confirmed absent on the router.

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
