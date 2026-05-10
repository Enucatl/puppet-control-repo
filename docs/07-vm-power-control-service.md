# Secure VM Power Control Service

Date: 2026-05-10

## Summary

Expose a small REST service for starting and stopping Proxmox VM `200`, but
keep the service inside the VPN and behind per-user authentication. The
service should not expose Vault access, should not accept arbitrary targets,
and should treat every request as an auditable action from an identified user.

The current `docker/vault/scripts/wake_on_lan.py` script is useful as the
internal execution path, but it is not safe to publish directly as a network
service because it shells out with interpolated values and reads a powerful
secret path from Vault.

## Recommended Design

Use three boundaries:

1. Network admission
   - Keep the service reachable only from WireGuard/LAN/trusted WiFi.
   - Publish it through Traefik, not with a direct container port.
   - Keep the existing IP allowlist behavior as a coarse first filter.

2. User authentication
   - Require Authelia for every mutating request.
   - Bind the request to an authenticated LDAP user or group.
   - Prefer a dedicated `power-operators` group instead of reusing broad admin
     access.

3. Application authorization
   - Hardcode or allowlist the only managed target: VM `200`.
   - Do not expose host shutdown, arbitrary VM IDs, or container IDs in the
     first version.
   - Keep `start`, `stop`, and `status` as the only public operations.

## Service Shape

Expose a small REST API:

- `GET /v1/status`
- `POST /v1/vm/200/start`
- `POST /v1/vm/200/stop`

The service should return only operational status. It should not leak Vault
errors, internal command output, or secret material.

For humans, a minimal web UI could be added later, but the first version should
stay API-only to keep the attack surface small.

## Execution Flow

`start` should:

1. Acquire a single-flight lock so concurrent requests cannot race.
2. Check whether the Proxmox host is already up.
3. If the host is down, send Wake-on-LAN.
4. Retrieve the unlock password from a dedicated Vault path using a service
   identity, not a user token.
5. Unlock the host through the existing Dropbear path.
6. Wait for Proxmox SSH to come back.
7. Start VM `200`.

`stop` should:

1. Shut down VM `200` gracefully.
2. Return the result of that action.
3. Avoid host shutdown in v1.

`status` should:

1. Report whether Proxmox SSH is reachable.
2. Report whether VM `200` is running.
3. Never include sensitive internal details.

## Vault and Credentials

Move the unlock password out of the broad `kv/puppet` path into a dedicated
secret such as `kv/power-control/proxmox-cortex`.

Use a narrow Vault policy that can only read that path. Do not grant list,
write, or admin capabilities to the controller service.

Prefer a service credential with a short-lived Vault token, such as cert auth
or AppRole, rather than reusing a human user session.

## Security Notes

- Replace shell-interpolated subprocess calls with argument-list invocation.
- Treat Traefik and Authelia as a gate, not as the only trust boundary.
- Log the authenticated user, source IP, request ID, action, and result for
  every mutating request.
- Add rate limiting for mutating endpoints.
- Keep direct access to the container port closed.

## Validation

Test the service against these cases:

- authenticated VPN user can start VM `200`
- authenticated VPN user can stop VM `200`
- unauthenticated request is rejected
- request from outside the VPN is rejected
- request for any target other than VM `200` is rejected
- Vault failure returns a clean error without leaking secrets
- Dropbear timeout and Proxmox timeout are handled cleanly
- concurrent start requests do not race

## Assumptions

- `start` and `stop` are the only exposed power actions.
- VM `200` is the only allowed target.
- “Turn off” means graceful shutdown of the VM, not host power-off.
- Every request must be tied to an authenticated user.
- VPN access alone is not sufficient without per-user authentication.

