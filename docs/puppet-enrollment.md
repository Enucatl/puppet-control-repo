# Puppet enrollment

Provisioning creates a two-hour Vault service token for one exact Puppet
certname. It has only `puppet-enrollment`, which permits
`auth/token/lookup-self`. It is nonrenewable, has one use, excludes the default
policy, and is an orphan so the provisioning administrator's identity policies
are not inherited. These settings use the native
[Vault token API](https://developer.hashicorp.com/vault/api-docs/auth/token).

Autosign accepts `<host>.home.arpa` and `<host>.dev.home.arpa`. The signed CSR
must have exactly one common name matching the requested certname and may
include only that same DNS name as a SAN. Autosign makes one `lookup-self`
request, requiring the enrollment-only policy and matching `meta.certname`.
Vault enforces consumption and concurrent-use protection. No local token state
or separate revocation request is involved.

## Rollout

1. Install the new policy by running
   `docker/vault/scripts/08-vault-puppet-policy.sh` with an administrator Vault
   login and the trusted Vault CA bundle available.
2. Deploy `scripts/autosign.py` and the `proxmox/` provisioning changes together.
   The autosign `--policy` override has been removed.
3. Rebuild the Ubuntu VM template with the updated cloud-init configuration so
   cloned VMs start with Puppet disabled. Guest cloud-init masks the service
   during package installation and unmasks it only after enrollment is configured.
4. Replace outstanding legacy bootstrap tokens. Ordinary `puppet` runtime
   tokens no longer authorize enrollment. Existing enrolled agents continue to
   use their certificates. Enrollment does not grant runtime secret access;
   runtime certificate issuance is restricted as described below.

VM provisioning derives the certname once from the chosen VM name and domain.
Host provisioning uses `hostname -f`. Enrollment stops the background Puppet
service and sets that exact certname before its first configured agent run.

## Replacement token

On the provisioning administrator's trusted shell, from the repository root:

```bash
export VAULT_ADDR=https://hcv.home.arpa:8200
export VAULT_CACERT=/etc/ssl/certs/ca-certificates.crt
# Use an existing administrator login; do not use the enrollment token here.
export VAULT_TOKEN="$(vault print token)"
source proxmox/lib.sh
create_vault_token node.home.arpa
# VM_TOKEN now contains the fresh token. Do not echo it or test lookup-self.
```

Pass that token securely to the intended host's enrollment script, with the
same certname (and the correct node type):

```bash
sudo proxmox/configure-puppet.sh docker "$VM_TOKEN" node.home.arpa docker.home.arpa
```

The required argument order is `node_type vault_token certname [puppet_server]`.
The helper verifies Vault TLS using the system trust store, or `VAULT_CACERT`
when set, and validates the creation response without spending the new token.

A successful Vault lookup consumes the token even if metadata validation then
fails. Treat any attempted Vault validation as spent and issue a replacement
after a failed enrollment. Failures before contacting Vault, such as malformed
CSRs or a mismatched CSR common name, may leave the token unused. A later CA or
catalog failure does not restore a consumed token.

Puppet 8 generates a fresh CSR using the updated attributes when it submits a
request. An existing pending request on the CA can block that submission. For a
failed enrollment that has not received a certificate, stop the agent and clean
the pending request on the Puppet CA before rerunning enrollment with a fresh
token:

```bash
sudo /opt/puppetlabs/bin/puppetserver ca clean --certname node.home.arpa
```

Enrolled-agent certificate rotation is a separate operation; this recovery
procedure is for a failed initial enrollment.

## Runtime certificate issuance

Puppet certificate login grants the `puppet` policy. Its `pki_int/issue/puppet`
endpoint uses the authenticated certificate's common name (the Vault cert-auth
identity alias), allowing that hostname and named subdomains beneath it. It
rejects wildcards, other nodes' names, localhost, and IP SANs. Both the requested
CN and DNS SANs are checked, including when `exclude_cn_from_sans` is enabled.

Only the authenticated `docker.home.arpa` identity can use
`pki_int/issue/puppet-docker.home.arpa`. That role also permits wildcards beneath
`docker.home.arpa`, including `*.docker.home.arpa` and
`*.service.docker.home.arpa`. The node-specific Hiera defaults select this
endpoint; other nodes use `puppet`. Changing a node's facts or requested endpoint
cannot change its Vault certificate identity or permissions.

The broad `general` issuance role remains available to administrators for manual
device issuance, but the `puppet` policy no longer grants access to it. The
existing `kv/data/puppet` and `kv/data/wolf` read permissions are unchanged.

Deploy the updated Hiera data and run
`docker/vault/scripts/08-vault-puppet-policy.sh` with administrator credentials
against the same Vault. The script creates both roles and then replaces the
shared policy; it can be rerun on an existing installation. The `cert` auth mount
and `pki_int` engine must already exist. The cert-auth setup scripts also require
the login certificate CN to match an admitted node, in addition to its DNS SAN.

Policy changes apply to existing tokens immediately. Cached catalogs that still
use `general` must be refreshed before they can renew certificates. Previously
issued certificates remain valid until expiry or revocation.

## Verification

```bash
uv run --frozen pytest -q tests/test_autosign.py tests/test_puppet_enrollment_vault.py tests/test_puppet_pki_vault.py tests/test_puppet_provisioning.py
```

The Vault tests launch a disposable localhost server and require the `vault`
binary. They cover replay, concurrent use, expiry, consumed mismatches, and
permission isolation when the issuer has identity policies. Provisioning tests
use dummy credentials; they require `jq` and `envsubst`. CI installs these
tools and runs these checks on enrollment-related changes.
