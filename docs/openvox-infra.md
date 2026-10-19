# Prepared OpenVox service in the infra stack (#12)

`docker/docker-compose.yml` now contains an opt-in `openvox` service. This is
deployment preparation; this stack has not been started as part of the
disposable rehearsal. Ordinary `docker compose up` excludes the service.
Activation requires `--profile openvox` or explicitly selecting the service.
Compose also permits explicit service selection to bypass a profile, so the
profile prevents accidental inclusion in a whole-stack start, not deliberate
activation by an operator.

Atlas's independent Docker/tooling bootstrap and pinned repository checkouts
have passed twice under #18 Stage A. See the
[bootstrap runbook](../proxmox/README.md#independent-atlas-tooling-bootstrap-18-stage-a)
and [live results](atlas-bootstrap-results.json). The infra stack is still
inactive on Atlas; adapting its service identity and explicit authority
initialization/enrollment belong to #13.

## Atlas endpoint requirements (#13)

The router reserves Atlas's current `10.0.0.158` address for MAC
`bc:24:11:4e:a4:9e`, which the creation script retains on rebuild. The host and
both new services use separate DNS identities at that same IPv4 address and
the verified stable ULA `fd6b:d722:1738:0:be24:11ff:fe4e:a49e`:

| Purpose | Identity / endpoint |
| --- | --- |
| Host and native agent certname | `atlas.home.arpa` |
| OpenVox service/server certname | `openvox.home.arpa`, direct TLS TCP `8140` |
| Vault service | `https://vault.home.arpa:8200` |

The [router provisioning](../provisioning/README.md#atlas-infrastructure-dns-and-dhcp)
declares these exact DNS records and the DHCP reservation. Reverse DNS identifies
the host as Atlas. Legacy `docker.home.arpa` and `hcv.home.arpa` continue to reach
the existing Docker host; existing agents and service consumers keep their old
endpoints until their migrations.

Before #13 activation, adapt the prepared OpenVox Compose hostname, Puppet
certname/server and certificate paths, PKI preparation, readiness and cache-flush
helpers to `openvox.home.arpa`. Point its Vault environment, Hiera backend and
the opt-in Vault configuration/API address at `vault.home.arpa`. Change these
together before generating identities. OpenVox and Vault leaf certificates must
contain their respective service DNS SANs, and Vault's scoped compiler cert-auth
role must authorize `openvox.home.arpa`. Atlas enrollment uses
`PUPPET_SERVER=openvox.home.arpa`, `VAULT_ADDR=https://vault.home.arpa:8200` and
native certname `atlas.home.arpa` with authenticated new-chain trust.

DNS alone does not make these services ready. The prepared service files below
still carry legacy names and must be adapted explicitly under #13; do not
globally replace legacy defaults or run the old combined Vault policy setup
against the new authorities. Fresh authority initialization remains explicit;
restore established identities on later rebuilds. The root private key stays
inside Vault, and the online OpenVox intermediate retains its own protected key.

## Prepared service implementation

The accepted `24001` OpenVox image is pinned by digest in
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
explicit public `/trust/root.pem` bundle. OpenSSL/native clients and the JVM
require TLS 1.3 and `X25519MLKEM768`; both listener and Java outbound protocols
are configured before startup. Vault may temporarily accept classical-only
clients under the operator-approved exception; capable clients must negotiate
the hybrid group. The existing infra Vault service is not upgraded or
reinitialized by selecting OpenVox.

The control repo's `hiera.yaml` uses `/trust/root.pem` explicitly and fails catalog compilation if
Vault authentication or lookup fails. These Hiera options are required in
addition to the autosigner's environment variables. This configuration belongs
to the new container deployment; keep it off the existing host server until
cutover. Rehearsal-only Hiera configuration does not validate this wiring.

Atlas's [publishing handover](../proxmox/README.md#atlas-production-publishing-and-legacy-separation)
separates code publication from container deployment. Its host bare repository is
`/opt/git/openvox/control.git`; the working checkout publishes `production` to
GitHub and this receiver. Legacy Docker publishes and fetches only its own old
bare repository. The receiver is public-readable (`2775` directory roots,
`core.sharedRepository=0664`) and Compose binds it read-only at `/git/control.git`.
The private `/opt/docker` tree retains mode `2770`. The remapped OpenVox user
(`999:999` inside, `100999:100999` on Atlas) can read public Git data; writable
deployment status remains on the separate `/git` volume. r10k uses the file URL
`file:///git/control.git`, with Git trust scoped to that exact repository in the
image. Before activation, connect host receives to the container's
r10k/types/cache-refresh helper and update startup hook validation. The publishing
stage creates no active receive hook and starts no service.

## Activation prerequisites

Do not activate this service until all of these conditions are met:

1. The new hierarchy has been prepared with the root key generated internally
   and retained inside Vault. Vault retains the root signing key. Its OpenVox intermediate is ML-DSA-44 with `CA:TRUE,pathlen:0`, its
   key stays in the online OpenVox state, and root signing material is absent from OpenVox and distribution-job mounts. The online Vault
   has its separate intermediate and a verified ML-DSA/TLS 1.3 listener identity.
   Do not run the legacy default modes of `01-pki-core-setup.sh` or
   `02-pki-intermediate.sh` to prepare this hierarchy: those describe the older
   PKI. Their explicit `--offline-root` paths are documented in
   [the root-custody and historical rehearsal notes](openvox-offline-root.md).
2. The new OpenVox identity has been explicitly prepared/imported on first
   deployment, or its established new identity restored on recovery, and verified.
   Server host private/public keys must be prepared with
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
   host Git receive hook dispatches into the container's `infra-post-receive`
   helper; the legacy host hook uses `sudo` and different paths. Installing
   hooks from inside the container is blocked by the read-only source mount.
   #13 must implement that dispatch and adapt the current startup hook check,
   which still expects the container helper itself in the bare repository.
5. Production outbound crypto/consumer compatibility, independent offline
   recovery, and migration gates are signed off separately. The complete
   disposable rehearsal is useful evidence, but does not activate this service
   or establish every production compatibility requirement.
6. Atlas uses a distinct address and direct port 8140 while Docker's existing
   `puppetserver.service` continues serving legacy agents. #13 enrolls Atlas,
   then Aperture and Cognition as they migrate; it does not retire the old
   server or move legacy agents. Endpoint handoffs and old-service retirement
   follow the staged [migration plan](26.04-migration-plan.md).

| External volume | Container path | Contents |
| --- | --- | --- |
| `infra_openvox_ssl` | `/etc/puppetlabs/puppet/ssl` | Server identity and public trust/cache |
| `infra_openvox_ca` | `/etc/puppetlabs/puppetserver/ca` | Intermediate key, signed certificates, requests, CRLs, inventory |
| `infra_openvox_code` | `/etc/puppetlabs/code` | Deployed production/dev environments |
| `infra_openvox_data` | `/opt/puppetlabs/server/data/puppetserver/server_data` | Mutable server data, facts/reports and agent state |
| `infra_openvox_gems` | `/opt/puppetlabs/server/data/puppetserver/jruby-gems` | Operator-installed JRuby gems |
| `infra_openvox_git` | `/git` | Writable deployment status; host bare repository is a separate read-only bind at `/git/control.git` |
| `infra_openvox_r10k_cache` | `/var/cache/r10k` | Deployment cache |
| `infra_openvox_trust` | `/trust` (read-only) | `root.pem`, `root.crl`, `openvox-chain.pem` public bundles |

Persistent volumes are external, so `docker compose down -v` does not delete
them. Keep a verified backup of all eight volumes, exact images and matching
configuration. Logs in the temporary log directory disappear on recreation;
forward container logs to the existing centralized logging service. This
profile does not introduce an automatic renewal/root ceremony: schedule and
verify renewal before the configured one-day leaf expires.

## Explicit preparation and activation commands

These commands describe the preparation interface on the selected target
daemon; #18 must compose them into the scripted Atlas bootstrap with declared
inputs and no manual guest setup. They have not been executed against production.
Never source a production `.env` while
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

Submit only the CSR to the Vault-held root signer, sign the intermediate, then place
the public `root.pem`, `root.crl` and `openvox-chain.pem` (intermediate then root)
in the trust volume through authenticated bootstrap/import. Obtain the
SHA-256 fingerprint of the root certificate DER from the authenticated root
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
intermediate key, authority constraints and issuer lifetime. The helper uses
the packaged CA CLI's existing CRL import behavior and signs only the new intermediate's own CRL, invokes the packaged CA CLI,
then verifies the imported server key pair/lifetime and persists the complete
listener chain. The root private key is never an input. Restored state skips
fresh preparation/import and retains its established authority. A partial
failed import requires inspection/restoration; the helper never deletes it to
permit a retry. The rehearsal's `pki.rb` uses rehearsal names and must not be
run on this production identity.

Build and prepare code only after restoring the external volumes. The host
receive-hook dispatch and startup check described above are separate #13 work;
the container cannot install a hook in its read-only source repository.
These commands select just OpenVox, start no dependency, and publish no port
during `run`:

```sh
docker --host unix:///var/run/docker.sock compose -f docker/docker-compose.yml \
  --profile openvox build openvox
docker --host unix:///var/run/docker.sock compose -f docker/docker-compose.yml \
  --profile openvox run --rm --no-deps --entrypoint bash openvox -euc '
    test -s /git/control.git/HEAD
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
Vault-rooted deployment therefore uses the explicit preparation command above.

This wiring was checked by sanitized Compose rendering, activation/persistence
assertions and the missing-state startup check. The fresh-import self-check
also ran against the accepted image with separate disposable volumes, no
network and a read-only root filesystem. It proves actual packaged CA import,
key preservation, seed-only output, and rejection of wrong root pins, short
issuers and replacement attempts. Its test
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
followed by its separate issuing intermediate and Vault-held root. Client
certificates are requested when available but are optional at the TLS listener,
so token-based enrollment can work before a node has its first certificate.
Vault's certificate auth method must still validate and authorize presented
certificates. These are supported
[Vault listener settings](https://developer.hashicorp.com/vault/docs/configuration/listener/tcp),
including the explicit protocol bounds. The raw storage endpoint stays disabled.

Before activation, independently verify the public root pin, the signed Vault
leaf/private-key pair, complete chain, validity and applicable CRLs. The files
must be readable by the existing Vault UID/GID 100. The root signing key remains
inside Vault's protected PKI storage; applications receive
only public root material. Keep recovery inputs in separately protected custody.
Changing listener configuration does not migrate or replace existing PKI state.
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
and expiry checks. Any authenticated TLS key replacement must atomically
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

## Standard CRL behavior

The deployment uses the existing OpenVox/Vault CRL behavior. It adds no strict
outbound CRL checks, shortened lifetime, publisher, AppRole, refresh timer,
startup dependency or expiry alert. Native enrollment retains the existing
`certificate_revocation=leaf` setting and the normal Puppet refresh interval.
The packaged CA continues to generate and maintain its own CRLs. Existing
certificate-chain, hostname and validity verification remains enabled.

The root key stays inside Vault. Custom CRL distribution and freshness policy
are outside #12's requirements; they are not prerequisites for ML-DSA support.
