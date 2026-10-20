# Puppet enrollment

The #12 fresh-chain path uses accepted OpenVox packages and a Vault-held ML-DSA-44
root. Install with `proxmox/install-openvox.sh` and supply operator-authenticated
public CA trust before enrollment. Guest/native provisioning takes its path
from `OPENVOX_CA_CERT`; guests receive it at
`/etc/puppetlabs/puppet/ssl/certs/ca.pem`. The shared helper sets and verifies
`key_type=mldsa44`, loads native TLS/seed policy, and refuses an existing
non-ML-DSA key. #13 enrolls Atlas, then Aperture and Cognition as they migrate.
Existing hosts stay on the old server until their own migration; changing the
setting does not rotate an old key. See the
[container plan](openvox-container-migration-plan.md).

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

Apply these steps through scripted bootstrap on the new Atlas stack. They do
not authorize changing the old server or enrolling legacy hosts. Atlas base
creation does not require a live enrollment token; enrollment follows deployment
of its own OpenVox/Vault services. See the [migration plan](26.04-migration-plan.md).

For the new-chain compiler, set `VAULT_ADDR` to the direct verified HTTPS Vault
endpoint and `VAULT_CACERT` to authenticated root trust. The autosigner rejects
plaintext Vault endpoints. Token-only enrollment uses server-authenticated TLS
without a client certificate. Hiera uses its explicit `ca_trust` option.

Existing CRL behavior is retained. No custom outbound CRL bundle, publisher,
refresh job or new revocation-enforcement policy is required for this migration.

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

### Atlas

After the verified final IPA import, run from Atlas's clean published checkout:

```bash
sudo python3 proxmox/atlas-enroll.py
```

The shared `proxmox/install-openvox.sh` provisions the accepted OpenVox agent
and applicable native curl/IPA library fixes from signed APT repositories.
Package builds, patches and release instructions live in
[openvox-packages](https://github.com/Enucatl/openvox-packages/blob/main/docs/native-client.md). No local `.deb`
artifact directory or manual package copying is required. Ubuntu 26.04 and
Debian 13 use the signed [native-client-2-1 release](https://github.com/Enucatl/openvox-packages/releases/tag/native-client-2-1).
Ubuntu 24.04 retains its existing OpenVox installation path and is outside this
native-client backport matrix. The OpenVox agent remains the accepted 24001
build; the companion archive does not replace the server image.
Fresh and repeated shared-installer runs passed in isolated Ubuntu 26.04 and
Debian 13 containers, using both public signed repositories, the exact package
versions and ML-DSA/RSA/ECDSA checks. Only `systemctl` was stubbed because these
containers do not run systemd; actual new-host enrollment remains a separate
step. See [compatibility and provisioning evidence](https://github.com/Enucatl/openvox-packages/blob/main/tests/debian-ipa-compat/results.json).

The curl fix handles digestless ML-DSA signatures in Kerberos HTTPS negotiation;
the IPA library fix reads ML-DSA SubjectPublicKeyInfo without asking
`cryptography.public_key()` to decode the algorithm. TLS certificate and
hostname verification remain enabled. Puppet still installs and enrolls the IPA
client. An explicitly incomplete installation is recovered only after its
configured identity matches Atlas.

Documented helper runs and scheduled agent runs preload `openvox-child-policy.rb`: Puppet
initializes its accepted strict TLS and key-writing policy, then gives spawned
commands the operating system's TLS policy. This lets APT and public downloads
work without changing Puppet's initialized TLS contexts. Atlas's managed
systemd drop-in retains the same preload for scheduled runs.

The script checks enrollment state, installs authenticated public CA trust, runs
the shared package installer, configures scoped ML-DSA-44
runtime issuance in the new Vault, creates a single-use enrollment token and
passes it through stdin to the shared helper. Atlas enrolls as `atlas.home.arpa`
against `openvox.home.arpa`. Its catalog installs the FreeIPA client and runs
`ipa-client-install` with `ipa.home.arpa` and the authenticated CA file. It also
creates the website certificate directories and certificates from Atlas's node
data. Existing Docker clients retain their original node data and endpoints.
Private enrollment output is retained under `/root/openvox-infra`. Repeating
the command with an existing certificate resumes an ordinary catalog run. It
checks the configured certname and server, preserves the identity, and does not
create another enrollment token.

### Other provisioning

On the provisioning administrator's trusted shell, from the repository root:

```bash
export VAULT_ADDR=https://vault.home.arpa:8200
export VAULT_CACERT=/etc/ssl/certs/ca-certificates.crt
# Use an existing administrator login; do not use the enrollment token here.
export VAULT_TOKEN="$(vault print token)"
source proxmox/lib.sh
create_vault_token node.home.arpa
# VM_TOKEN now contains the fresh token. Do not echo it or test lookup-self.
```

Standard VM and host provisioning already runs `proxmox/install-openvox.sh`
before enrollment. For a separately prepared host, run that shared installer
first with authenticated public CA trust available. It installs the accepted
OpenVox agent and native prerequisites for the supported OS, keeping Puppet
masked until enrollment.

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
device issuance, but the `puppet` policy no longer grants access to it or to KV.

Deploy the updated Hiera data and run
`docker/vault/scripts/08-vault-puppet-policy.sh` with administrator credentials
against the same Vault. The script creates both roles and then replaces the
shared policy; it can be rerun on an existing installation. The `cert` auth mount,
its existing `puppet` certificate role, and `pki_int` engine must already exist.
The cert-auth setup scripts also require
the login certificate CN to match an admitted node, in addition to its DNS SAN.

Policy changes apply to existing tokens immediately. Cached catalogs that still
use `general` must be refreshed before they can renew certificates. Previously
issued certificates remain valid until expiry or revocation.

## Runtime secret access

The shared `puppet` certificate role grants only scoped certificate issuance.
Hiera explicitly logs in as `puppet-server`, whose policy reads only
`kv/data/puppet`. Vault restricts this login to the Puppet CA and the exact
`docker.home.arpa` certificate CN. This is the current compiler identity; the
Docker host's agent shares that identity and therefore the same access boundary.

The `puppet-wolf` login reads only `kv/data/wolf`, and accepts only
`docker.home.arpa` and `proxmox.home.arpa` certificates from that CA. The Docker
wake script and Proxmox backup/vLLM workflows select this role explicitly. Wolf's
existing dedicated `wolf` certificate role is unchanged. KV permissions cover
the whole document, including all fields; `vault kv get -field=...` does not
restrict authorization to that field. Administrative bootstrap scripts still
use an administrator login.

For migration, deploy the Hiera `auth_name` and runtime login changes together
with `08-vault-puppet-policy.sh`. The script installs the restricted roles before
removing both KV grants from the shared policy. Existing shared-policy tokens
lose their KV reads immediately, so refresh deployed workflow scripts before
their next run. A cached CLI token without the new permission causes those
scripts to reauthenticate under `puppet-wolf`. Re-running the CA import also
refreshes the restricted roles' trusted CA.

Verify an ordinary node cannot read either KV document or log in under either
restricted role; verify the compiler can read `kv/puppet`, the runtime consumers
can read `kv/wolf`, and ordinary certificate renewal still works. The disposable
Vault tests below exercise these boundaries, including tokens issued before the
policy replacement and certificates with forged privileged DNS SANs.

## Verification

```bash
uv run --frozen pytest -q tests/test_autosign.py tests/test_puppet_enrollment_vault.py tests/test_puppet_pki_vault.py tests/test_puppet_provisioning.py
```

The Vault tests launch a disposable localhost server and require the `vault`
binary. They cover replay, concurrent use, expiry, consumed mismatches, and
permission isolation when the issuer has identity policies. Provisioning tests
use dummy credentials; they require `jq` and `envsubst`. CI installs these
tools and runs these checks on enrollment-related changes.

### Server-controlled catalog roles

Catalog role membership comes from a server-controlled symlink under
`data/roles/nodes/<full-certname>.yaml` pointing to the shared role YAML. For
example, `data/roles/nodes/docker.home.arpa.yaml` points to `../docker.yaml`.
Add the link before provisioning a new node that needs a role; put any
node-specific settings in `data/nodes/<full-certname>.yaml`. The enrollment
`node_type` argument still supplies an informational fact; it no longer authorizes a role.
Unclassified certificates receive only common and OS configuration. A certificate
for `docker.example.org` cannot select `docker.home.arpa` node configuration.

OS name and family facts may select OS defaults, but cannot contain path
separators or traversal segments. Docker-only MaxMind credentials are looked up
only for enabled GeoIP consumers, so unused class parameters do not expose them
to ordinary nodes. Shared enrollment, SMTP, and Beszel credentials remain in
catalogs for the nodes that require them.
