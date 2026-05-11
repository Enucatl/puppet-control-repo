# Wolf Power Control Service

Date: 2026-05-11

## Summary

`wolf` is a small authenticated control service for a fixed Wolf session.
It lives in the `infra` compose project, is published only through Traefik,
and is gated by Authelia plus a dedicated FreeIPA group.

The service does not accept arbitrary targets or shell commands. It controls
one fixed session:

- Proxmox host: `proxmox-cortex.home.arpa`
- VM: `200`
- guest: `complex.home.arpa`
- Wolf compose stack: `/opt/docker/wolf`

The implementation uses an explicit state machine for controller lifecycle
(`idle`, `starting`, `running`, `stopping`, `failed`) and separately probes
observed reality before deciding what to do.

## What Changed From the Draft

- The service name is `wolf`, not `wolf-power-control`.
- Proxmox control uses the HTTPS API with a token read from Vault.
- No Proxmox SSH identity is used.
- SSH remains only for the Dropbear initramfs unlock path.
- The unlock secret comes from `kv/wolf`, field `proxmox-cortex`.
- The Proxmox API token is stored in the same `kv/wolf` secret, field
  `proxmox-cortex-api-token`.
- The user-facing endpoint set is:
  - `GET /v1/session/status`
  - `POST /v1/session/start`
  - `POST /v1/session/stop`
- A minimal HTML UI is exposed at `/` with Start, Stop, status, authenticated
  user, and timeout remaining.
- The controller logs authenticated user, source IP, request ID, action,
  result, and stop reason.

## Runtime Boundaries

1. Network access
   - Exposed only through Traefik on `wolf.${DOCKER_DOMAIN}`.
   - Protected by `authelia@docker` and the shared Traefik allowlist.
   - The container is attached only to `traefik_proxy`.
   - The app rejects requests unless the peer IP matches a DNS-resolved
     trusted proxy hostname from `WOLF_TRUSTED_PROXY_HOSTS`.

2. User authentication
   - Friend login is a normal FreeIPA user.
   - Access is limited to the `wolf-operators` group.
   - The FreeIPA account is created with a temporary password and `nologin`
     shell.

3. Service credentials
   - Vault cert auth is used only to read `kv/wolf`.
   - Proxmox API access uses a token from the same Vault path.
   - Dropbear SSH uses a dedicated unlock keypair.
   - Dropbear host identity is pinned with a Docker secret known_hosts file.

## Session Behavior

### Start

`POST /v1/session/start` runs the fixed sequence:

1. Reconcile observed state.
2. Check whether the Proxmox API is already reachable.
3. If not reachable, read the unlock password from Vault.
4. Send Wake-on-LAN to `proxmox-cortex`.
5. Unlock the host through Dropbear SSH using the dedicated key.
6. Wait for the Proxmox API to return.
7. Query VM `200` state through the Proxmox API.
8. Start VM `200` if needed.
9. Wait for guest SSH readiness on `complex.home.arpa`.
10. Run `cd /opt/docker/wolf && docker compose up -d` inside the guest
    through the Proxmox guest agent.
11. Start the 4-hour session timer.

### Stop

`POST /v1/session/stop` runs:

1. `cd /opt/docker/wolf && docker compose down`
2. Graceful VM `200` shutdown through the Proxmox API
3. Host shutdown through the Proxmox API

If the timeout fires, the same stop path is used and the log entry carries
`reason=timeout`.

## Reconciliation

The controller keeps two views of the world:

- controller phase: what this app believes it is doing
- observed state: what Proxmox, the guest, and Wolf are actually doing

Observed probes currently cover:

- Proxmox API reachability
- VM `200` status
- guest SSH reachability
- Wolf compose state through the guest agent

If observed state shows that the session disappeared externally, the
controller marks the session as lost rather than pretending its cached state
is still true.

## Persistent Session State

The controller persists its session state in `/state/session.json`, backed by
the `wolf_state` Docker volume. This preserves the app-owned session deadline
across container restarts. If the container restarts while a session is
running, it reloads the deadline and reschedules the timeout.

Transient `starting` or `stopping` phases are marked as `failed` after a
restart, because the process can no longer know which step was interrupted
without observing reality again.

## FreeIPA User Script

The operator bootstrap script is:

- [docker/wolf/scripts/30-create-wolf-operator.sh](/opt/docker/puppet-control-repo/docker/wolf/scripts/30-create-wolf-operator.sh)

It:

- creates `wolf-operators` if needed
- creates or updates the user with `/usr/sbin/nologin`
- assigns a temporary password
- adds the user to `wolf-operators`
- prints the temporary password once

It does not add SSH keys, sudo rules, Docker group membership, or admin
membership.

## Vault Bootstrap

The Wolf Vault setup script is:

- [docker/wolf/scripts/20-vault-wolf.sh](/opt/docker/puppet-control-repo/docker/wolf/scripts/20-vault-wolf.sh)

It creates a narrow Vault policy for:

- `kv/data/wolf`
- `auth/token/lookup-self`

and binds cert auth for the `wolf` Vault login role.

## Bootstrap Checklist

These are the only manual secrets and credentials this service needs.

| System | Item | Create With |
|--------|------|-------------|
| Vault | `kv/wolf:proxmox-cortex` | `docker/wolf/scripts/20-vault-wolf.sh` or `vault kv put kv/wolf proxmox-cortex='…' proxmox-cortex-api-token='…'` |
| Vault | `kv/wolf:proxmox-cortex-api-token` | `docker/wolf/scripts/20-vault-wolf.sh` or `vault kv put kv/wolf proxmox-cortex='…' proxmox-cortex-api-token='…'` |
| Proxmox | `wolf@pve` API user and token | `docker/wolf/scripts/10-proxmox-token.sh` |
| Dropbear | `docker/wolf/secrets/dropbear_key` | `ssh-keygen -t ed25519 -f docker/wolf/secrets/dropbear_key -N '' -C wolf-dropbear` |
| Dropbear | `docker/wolf/secrets/dropbear_known_hosts` | `ssh-keyscan -p 2222 dropbear.proxmox-cortex.home.arpa > docker/wolf/secrets/dropbear_known_hosts` |
| FreeIPA | `wolf` operator account | `docker/wolf/scripts/30-create-wolf-operator.sh` |

### Vault Secret Values

The `kv/wolf` fields are:

- `proxmox-cortex`: the ZFS unlock password used by Dropbear initramfs
- `proxmox-cortex-api-token`: the full Proxmox API token string returned by
  `pveum user token add`

`docker/wolf/scripts/20-vault-wolf.sh` creates or updates `kv/wolf` and migrates
these fields from `kv/puppet` if they already exist there. The Wolf Vault
policy can only read `kv/wolf`.

Example:

```bash
vault kv put kv/wolf \
  proxmox-cortex='your-dropbear-unlock-password' \
  proxmox-cortex-api-token='wolf@pve!wolf=your-token-secret'
```

### Proxmox Token

Run this on the Proxmox host as root:

```bash
docker/wolf/scripts/10-proxmox-token.sh
```

Store the full `wolf@pve!wolf=<token-secret>` value printed by the script in
Vault under `kv/wolf:proxmox-cortex-api-token`.

The token is enough for VM lifecycle control and guest-agent execution. If
you later need to tighten it further, keep the token scoped to `/vms/200` and
`/nodes/proxmox-cortex` rather than broadening it to a cluster-wide role.

The Proxmox bootstrap uses two custom roles instead of one combined role:

- `WolfVmControl` on `/vms/200`
  - `VM.PowerMgmt`: start and shutdown VM `200`
  - `VM.Console`: guest-agent command execution path
  - `VM.Audit`: read VM status/config
- `WolfNodePower` on `/nodes/proxmox-cortex`
  - `Sys.PowerMgmt`: shut down `proxmox-cortex`
  - `Sys.Audit`: read node status

The split keeps VM privileges scoped only to VM `200`, and node privileges
scoped only to `proxmox-cortex`. A single combined role would likely work
today, but assigning that combined role at both paths is less clear and more
fragile if Proxmox privilege semantics change later.

`VM.Monitor` is intentionally not granted. It is broader than this service
needs and is being phased out in newer Proxmox releases.

### Dropbear Keypair

Generate the initramfs unlock keypair locally:

```bash
ssh-keygen -t ed25519 -f docker/wolf/secrets/dropbear_key -N '' -C wolf-dropbear
```

Then ensure the public key is present in
`data/nodes/proxmox-cortex.yaml` under
`profile::dropbear_initramfs::authorized_keys`, and apply Puppet on
`proxmox-cortex` so the initramfs key is rebuilt.

At container startup, the private key is copied from the Docker secret mount
to `/run/wolf/dropbear_key` with mode `0600`. The unlock SSH command uses that
private tmpfs copy because OpenSSH rejects overly permissive Docker secret
mounts as identity files.

Capture the Dropbear host key while the initramfs SSH endpoint is up:

```bash
ssh-keyscan -p 2222 dropbear.proxmox-cortex.home.arpa > docker/wolf/secrets/dropbear_known_hosts
```

The unlock SSH command uses this pinned host key file with
`StrictHostKeyChecking=yes`.

## Validation Notes

Relevant checks for this service:

- authenticated `wolf-operators` user can start and stop a session
- non-member FreeIPA user is denied
- unauthenticated requests are denied
- temporary-password flow works with Authelia and FreeIPA
- concurrent start/stop requests are serialized
- timeout triggers the same stop path
- startup failure cleans up any layers it started
