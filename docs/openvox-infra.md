# Prepared OpenVox service in the infra stack (#12)

`docker/docker-compose.yml` now contains an opt-in `openvox` service. This is
deployment preparation; this stack has not been started as part of the
disposable rehearsal. Ordinary `docker compose up` excludes the service.
Activation requires `--profile openvox` or explicitly selecting the service.
Compose also permits explicit service selection to bypass a profile, so the
profile prevents accidental inclusion in a whole-stack start, not deliberate
activation by an operator.

The accepted `18001` OpenVox image is pinned by digest in
`docker/openvox/infra.Dockerfile`; the derived image adds Git and r10k 5.0.3.
Provider/JDK/package policy remains owned by the accepted image. The service
runs as UID/GID 999, with a read-only image, dropped capabilities, bounded JVM
heap, writable temporary runtime/log directories, and a 60-second graceful
shutdown window. Its private `/tmp` tmpfs explicitly permits executable mappings
for JRuby native-library extraction; without `exec`, JFFI loses POSIX support
and the real hardened service fails before catalog compilation. Tini remains
PID 1 and forwards signals to the server process.
Provider policy and packaged gems are not hidden by a broad data-directory
mount.

The listener keeps the existing `docker.home.arpa` identity and publishes TCP
8140 directly. There is no HTTP proxy or TLS termination between agents and
OpenVox, or between OpenVox and `https://hcv.home.arpa:8200`. Vault trust is the
explicit public `/trust/root.pem` bundle. `VAULT_CRL` selects
`/trust/outbound-crls.pem`, containing the current signed root CRL and the CRL
of Vault's actual issuing intermediate (the separate FreeIPA/Vault intermediate,
not the OpenVox intermediate). OpenSSL/native clients and the JVM
require TLS 1.3 and `X25519MLKEM768`; both listener and Java outbound protocols
are configured before startup. Vault may temporarily accept classical-only
clients under the operator-approved exception; capable clients must negotiate
the hybrid group. The existing infra Vault service is not upgraded or
reinitialized by selecting OpenVox.

The control repo's `hiera.yaml` uses those same `/trust/root.pem` and
`/trust/outbound-crls.pem` files explicitly and fails catalog compilation if
Vault authentication or lookup fails. These Hiera options are required in
addition to the autosigner's environment variables. This configuration belongs
to the new container deployment; keep it off the existing host server until
cutover. Rehearsal-only Hiera configuration does not validate this wiring.

## Activation prerequisites

Do not activate this service until all of these conditions are met:

1. The new hierarchy has been prepared with the root key generated internally
   and retained inside Vault. Vault remains available for automated CRL signing. Its OpenVox intermediate is ML-DSA-44 with `CA:TRUE,pathlen:0`, its
   key stays in the online OpenVox state, and root signing material is absent from OpenVox and distribution-job mounts. The online Vault
   has its separate intermediate and a verified ML-DSA/TLS 1.3 listener identity.
   Do not run the legacy default modes of `01-pki-core-setup.sh` or
   `02-pki-intermediate.sh` to prepare this hierarchy: those describe the older
   PKI. Their explicit `--offline-root` paths are documented in
   [the root-custody and historical rehearsal notes](openvox-offline-root.md).
2. The existing OpenVox identity has been explicitly imported/restored and
   verified. Server host private/public keys must be prepared with
   `key_type=mldsa44` before an external CA import. The listener PEM contains
   the server leaf followed by intermediate and root certificates. The server
   leaf and all CRLs must still be valid, and signed leaf lifetimes must not
   exceed their issuer. Initial import uses `ca_ttl=1d`; the issuer must have
   more than one day remaining before import. The accepted CA CLI does not
   enforce that lifetime cap itself.
3. The eight external volumes below already exist, have been populated from
   verified state or explicit one-time setup, and are writable by UID/GID 999
   within the target daemon's user namespace. Compose refuses absent external
   volumes instead of creating an empty replacement. Startup rejects missing
   trust, CRL, Git/hooks, deployment markers, server keys or CA keys. Starting
   the service never initializes Vault, generates a replacement authority or
   imports an intermediate.
4. Production/dev code contains the accepted Puppetfile and enrollment helpers,
   and has been deployed using the container r10k configuration. The restored
   bare Git hook is replaced with `infra-post-receive`; the host hook uses
   `sudo` and different paths and is not compatible with this container.
5. Production outbound crypto/consumer compatibility, independent offline
   recovery, and migration gates are signed off separately. The complete
   disposable rehearsal is useful evidence, but does not activate this service
   or establish every production compatibility requirement.
6. DNS, firewall and the port-8140 cutover are planned. The existing host
   `puppetserver.service` cannot share the same port. Moving clients/production
   enrollment and retiring that service belong to the explicit #13 cutover.

| External volume | Container path | Contents |
| --- | --- | --- |
| `infra_openvox_ssl` | `/etc/puppetlabs/puppet/ssl` | Server identity and public trust/cache |
| `infra_openvox_ca` | `/etc/puppetlabs/puppetserver/ca` | Intermediate key, signed certificates, requests, CRLs, inventory |
| `infra_openvox_code` | `/etc/puppetlabs/code` | Deployed production/dev environments |
| `infra_openvox_data` | `/opt/puppetlabs/server/data/puppetserver/server_data` | Mutable server data, facts/reports and agent state |
| `infra_openvox_gems` | `/opt/puppetlabs/server/data/puppetserver/jruby-gems` | Operator-installed JRuby gems |
| `infra_openvox_git` | `/git` | `control.git`, executable post-receive hook and deployment status |
| `infra_openvox_r10k_cache` | `/var/cache/r10k` | Deployment cache |
| `infra_openvox_trust` | `/trust` (read-only) | `root.pem`, `root.crl`, `openvox-chain.pem` and Vault `outbound-crls.pem` public bundles |

Persistent volumes are external, so `docker compose down -v` does not delete
them. Keep a verified backup of all eight volumes, exact images and matching
configuration. Logs in the temporary log directory disappear on recreation;
forward container logs to the existing centralized logging service. This
profile does not introduce an automatic renewal/root ceremony: schedule and
verify renewal before the configured one-day leaf expires.

## Explicit preparation and activation commands

These are future operator commands on the selected target daemon. They have
not been executed against production. Never source a production `.env` while
testing; a Compose render can be checked without a daemon:

```sh
env -i PATH="$PATH" DOCKER_DOMAIN=example.invalid \
  docker compose --env-file /dev/null -f docker/docker-compose.yml \
  --profile openvox config --quiet
```

For fresh state, first build the derived image and explicitly prepare the
intermediate CSR and host key in the already-provisioned volumes. This helper
has no root-signing operation and refuses an already-imported CA or existing
server certificate. A preparation retry retains generated keys:

```sh
docker --host unix:///var/run/docker.sock compose -f docker/docker-compose.yml \
  --profile openvox build openvox
docker --host unix:///var/run/docker.sock compose -f docker/docker-compose.yml \
  --profile openvox run --rm --no-deps \
  --entrypoint /opt/puppetlabs/puppet/bin/ruby openvox /infra/infra-pki.rb prepare
```

Transfer only the CSR to the offline signer, sign the intermediate, then place
the public `root.pem`, `root.crl` and `openvox-chain.pem` (intermediate then root)
in the trust volume through the separate operator ceremony. Obtain the
SHA-256 fingerprint of the root certificate DER from the authenticated offline
operator record, not from the same untrusted import media. Set `ROOT_DER_SHA256`
to that 64-digit hexadecimal fingerprint, then run the one-time import:

```sh
docker --host unix:///var/run/docker.sock compose -f docker/docker-compose.yml \
  --profile openvox run --rm --no-deps \
  --entrypoint /opt/puppetlabs/puppet/bin/ruby openvox /infra/infra-pki.rb \
  import --root-sha256 "$ROOT_DER_SHA256"
```

Import validates the operator pin before changing CA state, ML-DSA algorithms,
seed-only private keys, the signed root/intermediate chain and matching
intermediate key, authority constraints, issuer lifetime and the signed,
unexpired root CRL. A root CRL that revokes the intermediate is rejected. The
helper signs only the new intermediate's own CRL, invokes the packaged CA CLI,
then verifies the imported server key pair/lifetime and persists the complete
listener chain. The root private key is never an input. Restored state skips
fresh preparation/import and retains its established authority. A partial
failed import requires inspection/restoration; the helper never deletes it to
permit a retry. The rehearsal's `pki.rb` uses rehearsal names and must not be
run on this production identity.

Build and prepare the code/hook only after restoring the external volumes.
These commands select just OpenVox, start no dependency, and publish no port
during `run`:

```sh
docker --host unix:///var/run/docker.sock compose -f docker/docker-compose.yml \
  --profile openvox build openvox
docker --host unix:///var/run/docker.sock compose -f docker/docker-compose.yml \
  --profile openvox run --rm --no-deps --entrypoint bash openvox -euc '
    test -s /git/control.git/HEAD
    install -m 0755 /infra/infra-post-receive /git/control.git/hooks/post-receive
    for environment in production dev; do
      /opt/puppetlabs/puppet/bin/r10k deploy environment "$environment" --modules \
        --config /infra/infra-r10k.yaml
      /opt/puppetlabs/bin/puppet generate types --environment "$environment" \
        --config /etc/puppetlabs/puppet/puppet.conf \
        --codedir /etc/puppetlabs/code \
        --vardir /opt/puppetlabs/server/data/puppetserver/server_data
    done
  '
```

When the explicit production cutover is approved and prerequisites are met:

```sh
docker --host unix:///var/run/docker.sock compose -f docker/docker-compose.yml \
  --profile openvox up -d --no-deps openvox
docker --host unix:///var/run/docker.sock compose -f docker/docker-compose.yml \
  --profile openvox ps openvox
docker --host unix:///var/run/docker.sock compose -f docker/docker-compose.yml \
  --profile openvox exec -T openvox /opt/puppetlabs/puppet/bin/ruby /infra/infra-ready.rb
```

The health check verifies the local listener using its real DNS identity and
public root, checks the CA endpoint and requires the service status to be
`running`. It makes no credentialed initialization request. Stop without
destroying state using the same Compose selection and `stop openvox`.

Pushes to the persisted bare repository deploy only `production`/`dev`, generate
types and invalidate the environment caches over authenticated TLS. The server
certificate must carry the imported CA CLI authorization extension accepted by
the packaged admin API rules. A successful Git receive can hide a failed
post-receive hook; check `/git/deploy.status` equals `0` and inspect the deployed
environment's `.r10k-deploy.json` signature against the intended commit. The
hook's cache refresh fails closed if the live server is unavailable. Initial
offline deployment therefore uses the explicit preparation command above.

This wiring was checked by sanitized Compose rendering, activation/persistence
assertions and the missing-state startup check. The fresh-import self-check
also ran against the accepted image with separate disposable volumes, no
network and a read-only root filesystem. It proves actual packaged CA import,
key preservation, seed-only output, and rejection of wrong root pins, short
issuers, expired CRLs, revoked intermediates and replacement attempts. Its test
root key exists only in memory in the isolated signer process. With the
rehearsal tools image already built, repeat it locally with:

```sh
python3 docker/openvox/infra-pki-self-test.py
```

A production runtime start, host-port cutover and actual production catalog
application remain unperformed.

## Separate opt-in Vault configuration

`docker/openvox/infra-vault.override.yaml` is an explicit future Vault cutover
file. It is never included by the OpenVox profile automatically and does not
change the currently bound `docker/vault/config/vault-conf.hcl`. The override
pins the accepted Vault 2.2.0 image, replaces only the Vault configuration mount
and command, and keeps the existing `vault_data:/vault/file` file backend and
`vault_logs` volume. It mounts the certificate volume read-only.

The new `infra-vault.hcl` requires TLS 1.3 for both minimum and maximum versions,
serves `/certificates/vault_chain.crt` with `/certificates/vault.key`, and uses
`/certificates/ca.crt` for client trust. The chain file contains the Vault leaf
followed by its separate issuing intermediate and offline root. Client
certificates are requested when available but are optional at the TLS listener,
so token-based enrollment can work before a node has its first certificate.
Vault's certificate auth method must still validate and authorize presented
certificates. These are supported
[Vault listener settings](https://developer.hashicorp.com/vault/docs/configuration/listener/tcp),
including the explicit protocol bounds. The raw storage endpoint stays disabled.

Before activation, independently verify the public root pin, the signed Vault
leaf/private-key pair, complete chain, validity and applicable CRLs. The files
must be readable by the existing Vault UID/GID 100. The online storage must
contain only the intended intermediate/service state; the offline root key and
root recovery material must remain outside its volumes. Changing listener
configuration does not remove an old online root key from stored PKI state.
Use verified, backed-up state prepared through the separate migration/restore
procedure and review Vault-version compatibility before selecting this file.
The override does not initialize, unseal, generate, import or delete any issuer.

Render without a daemon or production `.env` first:

```sh
env -i PATH="$PATH" DOCKER_DOMAIN=example.invalid \
  docker compose --env-file /dev/null -f docker/docker-compose.yml \
  -f docker/openvox/infra-vault.override.yaml config --quiet
```

After the explicit Vault cutover is approved and established state/certificates
are prepared, select only Vault on the intended host:

```sh
docker --host unix:///var/run/docker.sock compose -f docker/docker-compose.yml \
  -f docker/openvox/infra-vault.override.yaml up -d --no-deps vault
```

Retain both `-f` arguments for subsequent Vault management. The opt-in
unsealer override mounts only the certificate volume's `public` subdirectory,
containing the authenticated Vault TLS public key `vault.pub.pem`; it cannot
read the TLS private key. Prepare that directory and public SPKI from the
already authenticated deployed TLS certificate during bootstrap/import, before
starting the unsealer. The legacy bootstrap certificate writer now publishes
this public pin atomically too; do not run legacy RSA root creation merely to
prepare a pin for the new hierarchy.

For only `/sys/health` and `/sys/unseal`, the unsealer authenticates the exact
locally pinned TLS public key with TLS 1.3. These requests deliberately do not
depend on current PKI CRLs or certificate expiry: a sealed Vault cannot publish
fresh CRLs or renew an expired listener certificate. `curl --insecure` is paired
with mandatory `--pinnedpubkey` in this explicit bootstrap mode; key-possession
verification remains mandatory. Missing/wrong pins fail before transmitting an
unseal share. Ordinary application/PKI clients retain full chain, hostname,
expiry and CRL checks. Any authenticated TLS key replacement must atomically
publish its corresponding public pin before switching the listener. This does
not add an automatic listener-certificate renewal workflow to the existing
bootstrap-only certificate writer; that lifecycle remains part of #13 cutover.

Never substitute
`vault operator init` when a restored instance is unexpectedly uninitialized.
Do not start the entire stack or its legacy setup services as a side effect of
this operation. The ordinary OpenVox activation command does not change Vault.

The exact HCL was started with the pinned Vault binary on separate disposable,
uninitialized file storage and a temporary signed ML-DSA test chain. Seven live
checks passed: configuration/listener startup, missing-identity refusal, storage
remaining uninitialized, hybrid selection with both groups offered, ML-DSA
server CertificateVerify, TLS 1.2 rejection and successful TLS without a client
certificate. Classical-only TLS 1.3 also succeeded, as allowed by the accepted
temporary policy. The clone used no network interface beyond loopback, no host
ports and no production volumes; its volumes and container were removed.
This checks listener configuration, not production identity/storage migration,
auth policies or UID-100 file ownership.

## CRL automation

The root signing key stays inside Vault. Vault remains online and performs
routine CRL signing automatically; there is no daily operator ceremony. This
supersedes the earlier physically offline rehearsal model. Nothing in this
checkout has enabled production jobs or changed production Vault settings.

The deployment adds **no continuously running CRL service**:

| Existing component or short job | Responsibility |
| --- | --- |
| Vault's PKI background worker | Rebuild root and Vault-intermediate CRLs automatically |
| OpenVox's existing CA maintenance | Maintain its issuer CRL with the configured 72-hour lifetime |
| `openvox-crl-refresh.timer`, every five minutes | Start one temporary container to validate public lists, update local trust files, and synchronize OpenVox/Vault consumers |
| `openvox-crl-client.timer`, per client every five minutes and before Puppet startup | Fetch the root and OpenVox lists directly from the existing CA endpoints and install a verified bundle |
| Shared failure-mail unit | Notify through existing mail infrastructure when a job fails |

There is no separate mirror container, CRL DNS name, Traefik route, incoming
publication volume, or publisher rotation scheduler. The job only mounts the
existing trust volume plus configuration and narrowly scoped machine credentials.

### Automatic signing and public downloads

Vault serves CRLs without authentication at `pki/crl/pem` and `pki_int/crl/pem`.
The OpenVox issuer list is public at
`/puppet-ca/v1/certificate_revocation_list/ca`. Clients fetch these existing
endpoints; a CRL URL is not itself trusted evidence. The updater verifies the
ML-DSA signatures against locally pinned CA keys, complete issuer/root pairing,
expiry, issuer lifetime, and persistent CRL numbers before replacing any bundle.
It rejects older numbers or different bytes at the same number. It never obtains
trust keys from these downloads or sends a credential with a CRL download.

Configure both Vault mounts with `expiry=72h`, `auto_rebuild=true`,
`auto_rebuild_grace_period=71h55m`, `enable_delta=false`, and `disable=false`.
The grace setting makes rebuilding due about five minutes after generation;
Vault checks roughly once per minute. This maintains complete CRLs, including
new revocations, without our job calling either rotation endpoint. Keeping
72-hour validity allows recovery from a short service outage. The worker runs
even if the external distribution job is stopped. Enabling automatic rebuild
means a revocation is picked up by that worker rather than causing an immediate
full rebuild itself.

The root URL is configurable so a separately hosted Vault can also serve it.
The supplied setup helper configures both mounts on the same Vault instance,
matching the existing deployment. A separate root instance needs the same CRL
settings applied by its own authenticated bootstrap; routine downloads still
need no credentials.

### One-time deployment configuration

1. Build/publish/pin the CA/library changes and the reviewed Hiera transport
   changes before activation. `ca.conf` requests `crl-ttl: "72h"`; the old
   accepted image ignores this and produces CRLs the updater rejects. The new
   CA bounds initial, revoked and renewed CRLs by issuer expiry and normalizes
   imported long lists. The final infrastructure image also installs the narrow
   root-CRL import authorization rule described below.
2. Restore the established CA identities and the trust volume's pinned root,
   OpenVox chain, Vault chain and initial valid CRLs. Do not initialize a new
   authority during a restart or recovery. Protect the persisted CRL-number
   state along with these files; it is the rollback barrier.
3. Declare `profile::openvox_crl_refresh` on the publication host with `config`
   matching `docker/openvox/crl-publisher.json` and the actual deployment
   `project_dir`. It creates `/etc/openvox-crl/publisher.json` and the protected
   credentials directory. Container UID/GID 999 maps to host 100999 on this
   installation; the class has explicit overrides for different mappings.
4. Run `modules/profile/files/openvox/configure-crl.rb CONFIG ADMIN_TOKEN_FILE`
   once through the accepted Ruby runtime in the authenticated deployment
   bootstrap. Use the strict `OPENSSL_CONF`, the same pinned root/current Vault
   CRLs, a protected administrator-token file, and a writable credentials
   directory. The helper configures both PKI workers, installs its sibling
   `crl-publisher-policy.hcl`, enables/reuses AppRole, and provisions the machine
   RoleID/SecretID as mode 0600 files. It refuses a partial credential pair and
   preserves an established pair on reruns. Paths in CONFIG must be valid in
   that execution environment; the example uses container paths. Run setup as
   the mapped publisher identity so the resulting files are readable by its
   normal read-only credentials mount. No administrator token is retained in
   the publisher configuration or its routine mounts.
5. Set `OPENVOX_CRL_PUBLISHER_CERTNAME` to the enrolled native host identity used
   by this job. The infrastructure CA authorization rule adds access for that identity
   only to the exact root-CRL PUT endpoint, while retaining existing CA
   administrator access. The publisher unit copies its current
   native-agent certificate/key into the protected credential directory before
   each run, using mapped ownership and mode 0600. The native agent maintains
   certificate renewal; there is no separate manually renewed publisher
   certificate. Use the class's `client_cert`/`client_key` parameters if that
   agent uses nondefault paths. Keep its ordinary agent running.
6. On clients, declare `profile::openvox_crl_client` using
   `docker/openvox/crl-client.json`. Check `localcacert` and `hostcrl` against
   `puppet config print localcacert hostcrl --section agent`. The pinned CA file
   must contain the root and OpenVox intermediate. Install authenticated initial
   trust/CRLs before enrollment, and include the configuration/state in recovery.
   Enrollment uses `certificate_revocation=chain` and `crl_refresh_interval=0`.

The AppRole machine credential is nonexpiring and stored only in the protected
host directory; it is revocable and must be replaced if that host is compromised.
Each run logs in over fully verified TLS and obtains a fresh ten-minute batch
token in memory. The token grants only the two certificate-auth CRL writes,
`auth/cert/crls/openvox` and `auth/cert/crls/root`. It has no default policy,
secret access, certificate issuance/revocation, CA rotation or root-key access.
There is no periodic token-renewal job or routine credential replacement task.

The authenticated writes remain necessary because Vault's certificate-auth CRL
handling does not provide the signature/freshness/rollback validation used here.
The distribution job validates first, then updates the two fixed CRL entries.
OpenVox root imports use its existing authenticated API; identical root lists
are not imported twice.

### Failure behavior and monitoring

The job updates `/trust/outbound-crls.pem` for Hiera/autosign and `/trust/root.crl`
for the existing local handoff. Only the issuer evidence needed for outage
recovery is cached under `/trust/crl-cache`. Each complete bundle is replaced
atomically, with the rollback barrier persisted first. No private keys or tokens
are placed in those outputs. Hiera reads updated CRLs on new backend sessions;
autosign reads them on invocation. Routine refresh needs no JRuby restart.

A client downloads the root directly from Vault even when OpenVox is unavailable.
It can combine a new signed root revocation with its still-valid cached OpenVox
issuer CRL, install that bundle, and report the issuer outage. A revoked OpenVox
intermediate is therefore blocked without depending on that server to distribute
its own revocation. The publisher likewise installs validated root revocation
evidence before credentialed API synchronization. If the OpenVox issuer has
been revoked, the publisher still updates the healthy Vault certificate-auth
backend before reporting the unavailable issuer; it must not leave Vault
trusting that issuer. If Vault itself has a revoked TLS chain, no login
credentials are transmitted. Expired caches are rejected.

Health fails on a failed refresh, last success more than 15 minutes ago, or
expiry within 24 hours. The five-minute jobs invoke the shared failure mailer,
throttled to once per hour. Integrate local `status.json` checks and inactive
job/timer detection with existing monitoring, and verify the root mail alias.
Vault certificate auth itself does not reject stale CRLs, so alert delivery
matters. Puppet requests an initial refresh before starting, but a failed refresh does
not leave its daemon stuck behind a failed startup dependency. The existing
TLS checks continue to reject invalid or expired CRLs; a still-valid cache can
be used during an outage. The independent timer retries without requiring an
operator to restart Puppet. The job does not restart a running agent.

For Vault-issued certificates, normal propagation includes about five minutes
plus one worker tick to rebuild, five minutes to synchronize, and potentially
another five minutes for a client refresh. OpenVox revocation rebuilds its own
list immediately. Allow for the consumer's next request/run as well. Existing
TLS sessions and issued Vault tokens are not forcibly terminated by a CRL update.

Vault availability and its existing unseal/startup mechanism remain prerequisites.
This work does not change those production services. After a temporary publisher
outage, Vault continues rebuilding its lists and the publisher obtains a new
login token on its next run. Trust compromise, lost machine credentials or an
expired CA certificate are recovery/authority-lifecycle events, not routine CRL
publication. Do not bypass signature or revocation checks to recover.

See [Vault's automatic CRL rebuild configuration](https://developer.hashicorp.com/vault/api-docs/secret/pki#set-revocation-configuration)
and [AppRole machine authentication](https://developer.hashicorp.com/vault/api-docs/auth/approle).
