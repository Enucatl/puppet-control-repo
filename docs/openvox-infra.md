# OpenVox service in the Atlas infra stack (#12, #13)

`docker/docker-compose.yml` contains an opt-in `openvox` service. Atlas now runs
OpenVox, Vault and the Vault unsealer; all three services passed their health
checks on 2026-10-19 at deployed control revision `60da9a9`.
Ordinary `docker compose up` excludes OpenVox.
Activation requires `--profile openvox` or explicitly selecting the service.
Compose also permits explicit service selection to bypass a profile, so the
profile prevents accidental inclusion in a whole-stack start, not deliberate
activation by an operator.

Atlas's independent Docker/tooling bootstrap and pinned repository checkouts
have passed twice under #18 Stage A. See the
[bootstrap runbook](../proxmox/README.md#independent-atlas-tooling-bootstrap-18-stage-a)
and [live results](atlas-bootstrap-results.json). Explicit authority
initialization and final FreeIPA import are complete. Atlas's native OpenVox
agent is enrolled and its Docker-role catalog passed with 242 resources, zero
failures and zero skipped resources. OpenVox installed and enrolled the IPA
client against `ipa.home.arpa`. An automatic daemon run also passed.
The final protected off-host kit contains 13 volumes and 19 checksum-verified
archives, including native enrollment and website identities. See
[enrollment evidence](atlas-enrollment-results.json) for its location and limits.
Independent restoration, isolated host reboot and image rollback passed before
the maintenance release at `60da9a9`. Its production push verified the host hook,
actual deployed commit, generated types and compiler cache refresh.
[Maintenance acceptance evidence](atlas-maintenance-results.json) records the
current deployment and recovery checks; [activation evidence](atlas-infra-results.json)
retains the initial identities and TLS checks.

The initialized Vault root certificate DER SHA-256 fingerprint is
`9e7498e776ad4ca805143244ff0b1a3bbf1f269891f3d7c40d40c94227f7c1ea`.
The initial recovery kit is stored off-VM at
`root@proxmox.home.arpa:/root/openvox-atlas-initial-20261019T162722Z`.
All 18 archives passed SHA-256 verification, including all twelve persistent
volumes. This initial backup is retained as a historical snapshot; the later
renewal kit supplied the independent restoration described below.
Both service names passed exact-name verification over IPv4 and IPv6 with
TLS 1.3, ML-DSA-44 signatures and `X25519MLKEM768`; OpenVox rejected an
`X25519`-only connection. Compiler certificate authentication can read the
bootstrap Hiera KV marker and is denied unrelated KV access. Run that scoped
check inside OpenVox with
`/opt/puppetlabs/puppet/bin/ruby /infra/infra-vault-check.rb`; it prints no login
token or secret contents.

The initial one-day leaf has been renewed in place and now expires at
**2026-12-18 16:51:50 UTC**. The 60-day leaf policy and 30-day renewal window
are active, checked by the enabled systemd timer every six hours. Root and
intermediate certificates and private keys are unchanged. The actual renewal,
served full chain, repeat no-op and refreshed off-VM kit are recorded in
[renewal acceptance evidence](atlas-openvox-renewal-results.json).

## Maintenance release and independent recovery

The maintained replacement-host entrypoint and current production recovery steps
are in [Atlas recovery](atlas-recovery.md). Protected acceptance scripts below are
historical evidence.

The maintenance fixes and shared Vault ACL files were published as `60da9a9`.
Both GitHub CI jobs passed: 568 enrollment/Atlas tests and 21 profile tests.
Atlas's checkout, bare production ref, deployed environment HEAD and successful
deployment receipt all matched that revision. The rebuilt derived image is
`sha256:c2f5ed3aef54f359786480db838cca0b513efa9ab93cbad11558110f0ff9aa5d`.
All three services are healthy; renewal remains enabled and successful. Root,
intermediate, listener certificate and private-key identities are unchanged.

Before publication, an isolated Docker host VM restored twelve authority
volumes from the eighteen verified renewal-kit archives. QEMU blocked outbound
access; Docker used an internal network and loopback service ports. The 27
acceptance checks covered scoped Vault access, renewal no-op/stopped recovery,
failed-restart retry, configuration/image drift, backup consistency and failed
service restoration. Image rollback was archived → rebuilt → archived → rebuilt.
A real VM reboot changed the boot ID while preserving the QEMU process,
automatically restored healthy services and retained a pending reload marker
until listener verification. Certificate/key bytes stayed unchanged throughout.
The first reboot attempt hit controller-host OOM; the repeat passed with a
2 GiB guest and zero guest OOM events. The disposable child disk was removed
afterward; earlier lab disks and all evidence were retained.

Protected transcripts, restore helpers and `RECOVERY-RUNBOOK.txt` are at
`root@proxmox.home.arpa:/root/openvox-atlas-maintenance-acceptance-20261019T180700Z-60da9a9`.
Its evidence archive SHA-256 is
`73e1b7096b99ada289ed04165c4fc9fc36ebe62fc99fc954ac71c646544ee8a8`.
Docker's containerd image store required repairing the saved OCI index to
reference all three genuine archived manifests before import; original image
blobs and archive checksums were preserved. The included `repair-oci.py`
handles configuration or manifest IDs. The isolated Vault model used its
archived architecture-specific manifest because the multiarch index was absent;
production retains the original pin. Follow the protected runbook for import.

The refreshed production kit is at
`root@proxmox.home.arpa:/root/openvox-atlas-maintenance-20261019T181027Z`.
All eighteen archives verified after transfer; its manifest SHA-256 is
`c3b8d8320099e7b357111580069cf9e8b955e70f2287618ee444ecc900337cef`.
Both protected directories are `0700`, with files `0600`. This latest kit was
checksum-verified rather than restored into another VM. The combined restore
script assembles demonstrated steps and was not rerun on a second fresh guest.
The guest used ordinary numeric ownership 999/100; production Docker remapping
was unchanged. Production host reboot, native-agent enrollment, full-role
application and physical-disk independence are outside this acceptance.
See [the sanitized results](atlas-maintenance-results.json) for adaptations.

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
the host as Atlas. Legacy `docker.home.arpa` continues to reach the existing Docker host.
Vault consumers use `vault.home.arpa` on Atlas.

The OpenVox Compose hostname, Puppet certname/server and certificate paths,
PKI preparation, readiness and cache-flush helpers use `openvox.home.arpa`.
Its Vault environment, deployed Hiera overlay and opt-in Vault configuration/API
address use `vault.home.arpa`. OpenVox and Vault leaf certificates must
contain their respective service DNS SANs, and Vault's scoped compiler cert-auth
role must authorize `openvox.home.arpa`. Atlas enrollment uses
`PUPPET_SERVER=openvox.home.arpa`, `VAULT_ADDR=https://vault.home.arpa:8200` and
native certname `atlas.home.arpa` with authenticated new-chain trust.

DNS alone does not make these services ready. Do not
globally replace legacy defaults or run the old combined Vault policy setup
against the new authorities. Fresh authority initialization remains explicit;
restore established identities on later rebuilds. The root private key stays
inside Vault, and the online OpenVox intermediate retains its own protected key.

## Scripted Atlas initialization (#13)

`proxmox/atlas-promote.sh` runs as root on Proxmox. It verifies VM 210's name,
MAC and ownership, replaces its disposable provisioning description with
`openvox-authority:atlas.home.arpa`, and records the promotion in the guest.
The development rebuild command then refuses the VM. Promotion must precede
any authority creation.

From Atlas's clean, published production checkout, as root:

```sh
python3 proxmox/atlas-infra.py initialize
```

This explicit command allocates twelve labelled external volumes, sets their
ownership through Docker's user namespace, initializes Vault and creates its
internal ML-DSA-44 root (`pki`, ten years) and separate service intermediate
(`pki_int`, five years). OpenVox generates its own intermediate key/CSR and
imports only its signed public chain. A locally generated, authenticated
bootstrap TLS identity protects Vault initialization; Vault then receives its
exact-name root-signed leaf (thirty days). Both authorities enforce issuer
lifetime limits. Initial OpenVox leaves and renewed leaves use 60 days; the root
and intermediate lifetimes remain ten and five years respectively.

Recovery JSON, root token, intermediate request, listener key and initialization
checkpoints live under root-only `/root/openvox-infra` (`0700`, files `0600`).
The unsealer receives only its key share and authenticated public TLS-key pin;
OpenVox receives public root trust, never Vault storage or root credentials.
Vault's compiler cert-auth role admits exactly `openvox.home.arpa`, with only
the existing read policy for `kv/data/puppet`. Fresh Vault starts with a harmless
Hiera marker, not copied legacy secrets or broad legacy issuance policies.
The compiler and enrollment ACLs are shared with the legacy setup through
`docker/vault/scripts/policies/puppet-server.hcl` and `puppet-enrollment.hcl`.

An interrupted explicit initialization retains keys and repairs TLS from saved
material. Once volume allocation completes, missing volumes require restoration.
A missing established issuer or changed root pin fails before replacement.
The signed services intermediate is saved before import. An interrupted import
reuses that exact certificate, checks the original Vault key and issuer identity,
and requires restoration if established request, certificate, key or issuer state
is missing.
After initialization, `initialize` refuses; normal startup is:

```sh
python3 proxmox/atlas-infra.py start
```

Startup waits for health and has no initialization or signing operation.
The script deploys production/dev, installs the scoped Hiera overlay, generates
types and installs the host post-receive dispatcher before starting OpenVox.
The code volume disables Docker's image-template copying so r10k starts with an
empty environment directory. An older volume containing exactly the packaged
template is preserved under `packaged-environments` before deployment; unknown
code is refused rather than moved or removed.
Subsequent receives repeat those deployment steps and invalidate compiler caches.
An unchanged push does not replay a failed deployment: explicitly replay its
ref-update input to the managed hook after correcting the scripted cause.
Replay must name the current branch commit; stale input is rejected. Deployment
handling is serialized and verifies the deployed environment's actual Git HEAD
before recording a successful receipt.

Use `proxmox/atlas-infra-backup.sh /scratch/vault/NEW_DIRECTORY` as root on Atlas
to stop the established services, archive their volumes, separate recovery
inputs, exact images, control/hardening configuration, public Git receiver and
daemon mapping, then restart established services and wait up to five minutes
for the running services to become healthy. Before IPA initialization this covers
three services and twelve volumes. Prepared IPA adds its original `infra_ipa_data`
volume, exact image and pinned base reference; its canonical credentials and
issuer checkpoints are included in the recovery-input archive. IPA is stopped
for a consistent copy and restarted only if it was running before backup.
Failure cleanup also checks service health;
failed recovery returns failure and requires explicit repair. Configuration
archives use the commit IDs captured in the manifest. Every output is private and
hash-listed. Copy the complete kit to a new root-only Atlas-specific directory
on Proxmox and verify its manifest. `/scratch` is temporary and its local copy
alone does not satisfy independent recovery. Never overwrite the #12 test kit.
A backup proves preservation only; restoration and unchanged-identity acceptance
remain separate checks before issue closure.

### Automated Vault and IPA transport renewal

Puppet installs `atlas-transport-renew.service` and its six-hour timer. After
initial authority setup, run the following once as root on Atlas to authorize
its existing native Puppet agent identity, then verify the first check:

```sh
python3 /opt/docker/puppet-control-repo/proxmox/atlas-transport-renew.py configure
systemctl start atlas-transport-renew.service
systemctl enable --now atlas-transport-renew.timer
```

`configure` uses the protected bootstrap root token only to create the policy
and certificate-auth role. Routine renewal uses Atlas's existing native agent
certificate and a five-minute token restricted to the Vault and IPA transport
signing endpoints. It reads no root token. Keep the native agent's own built-in
certificate renewal enabled.

Vault's 30-day leaf is renewed with ten days remaining; IPA's 90-day HTTP and
LDAP leaves are renewed with thirty days remaining. Native Ruby/OpenSSL creates
CSRs from the existing keys, validates the complete ML-DSA chain against the
saved root fingerprint, and preserves the subject and identity extensions. The
Vault public key stays unchanged, and its unsealer pin is republished with the
same key. Vault reloads TLS with SIGHUP; IPA certificates are installed through
`ipa-server-certinstall`'s own implementation and the affected `httpd` or
`dirsrv@HOME-ARPA` service is restarted. Its Directory Manager password and
certificate inputs are supplied on stdin and kept out of process arguments;
temporary container files are private.

Issued certificates are saved as `*.renewal-pending.json` before publication.
The canonical JSON and PEM files are updated together under the shared
maintenance lock. A failed install or reload retains the
pending certificate for retry without another issuance. The marker is removed
only after an authenticated, exact-name TLS handshake serves the saved leaf.
A regular check verifies all three listeners and does not restart services.
Backups preserve the current recovery inputs, pending state and installed units.
If Vault has already expired while the timer was disabled, operator recovery
must install a valid transport leaf before this job can authenticate to Vault.

### Automated OpenVox leaf renewal

Atlas startup installs `atlas-openvox-renew.service` and its systemd timer,
completes the first renewal check, then enables checks five minutes after boot
and every six hours afterward. Agents use OpenVox's built-in 30-day renewal
window. Atlas's separate native agent is not needed to renew the container's
`openvox.home.arpa` identity.

The job uses `/opt/puppetlabs/bin/puppet ssl renew_cert --if-expiring-in 30d`,
with an explicit staged `--hostcert` so the CLI's leaf-only write cannot replace
the listener's full chain. Before requesting renewal, the intermediate must
have more than 60 days remaining. The resulting certificate must retain the
ML-DSA-44 key, subject, SANs and other identity extensions, verify against the
existing issuer and stay within the issuer's lifetime. The validated leaf,
intermediate and root are published atomically. No authority or private key is
regenerated.

Only a changed leaf, or an unfinished prior reload, restarts OpenVox. After
readiness, a fresh exact-name TLS handshake must serve the same certificate as
the file before the durable reload marker is cleared. This allows retry after
an interrupted publish/restart; a no-op check does not restart the container.
The maintenance lock shared with backup, startup and publishing prevents those
operations overlapping. The lock does not gate Git receives; direct pushes
update refs and their post-receive deployments serialize separately. Startup
releases the lock before synchronously starting renewal. Renewal recovers a stopped
existing OpenVox container and waits for its health without reconciling Compose
configuration or image changes.
The CLI uses the accepted `certificate_revocation=leaf` agent policy.

Run `systemctl start atlas-openvox-renew.service` for an immediate check and
`systemctl list-timers atlas-openvox-renew.timer` to inspect the schedule. A
successful command alone is insufficient: the job verifies the served identity.
Its logs contain certificate metadata, never tokens or private keys. A renewal
causes one brief OpenVox restart; Vault and its unsealer keep running. This
procedure requires an unexpired current OpenVox leaf. Vault and IPA transport
certificates use the separate job above; CA renewal remains an operator task.

The updated kit at
`root@proxmox.home.arpa:/root/openvox-atlas-renewal-20261019T165202Z`
contains the renewed identity, unchanged authorities and installed unit files,
with all eighteen archives verified after transfer. The older initial kit is
retained. This renewal kit supplied the independent restoration above; the
latest post-maintenance kit and its verification are recorded there as well.

## Service implementation

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

The listener uses the new `openvox.home.arpa` identity and publishes TCP
8140 directly. There is no HTTP proxy or TLS termination between agents and
OpenVox, or between OpenVox and `https://vault.home.arpa:8200`. Vault trust is the
explicit public `/trust/root.pem` bundle. OpenSSL/native clients and the JVM
require TLS 1.3 and `X25519MLKEM768`; both listener and Java outbound protocols
are configured before startup. Vault may temporarily accept classical-only
clients under the operator-approved exception; capable clients must negotiate
the hybrid group. The existing infra Vault service is not upgraded or
reinitialized by selecting OpenVox.

The repository `hiera.yaml` uses `vault.home.arpa` and `/trust/root.pem` explicitly
and fails catalog compilation if Vault authentication or lookup fails. Deployment
uses this file directly; no container-specific Hiera overlay is needed.

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
image. Direct pushes update the receiver without a pre-receive lock gate. The
host post-receive hook dispatches to the container's r10k/types/cache-refresh
helper. The initialization script installs it after
the first deployment; startup verifies its exact content. Publisher checks the
deployment status and verified deployed commit after an updated production receive,
because Git itself can accept refs even when post-receive deployment fails.

## Authority and recovery requirements

Retain these requirements when operating or restoring the initialized service;
remaining recovery and migration acceptance is separate from its live status:

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
   exceed their issuer. Initial import uses `ca_ttl=60d`; the issuer must have
   more than 60 days remaining before import. The accepted CA CLI does not
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
   Startup checks the managed host dispatcher, whose reference is exposed by
   the existing read-only `/infra` mount.
5. Production outbound crypto/consumer compatibility, independent offline
   recovery, and migration gates are signed off separately. The complete
   disposable rehearsal does not establish every production compatibility
   requirement. Atlas authority restoration, isolated host reboot and image
   rollback passed the maintenance acceptance above; consumer and migration
   gates remain separate.
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
Atlas startup installs the leaf-renewal service and timer described above.
CA renewal remains a separate operator operation before issuer expiry.

## Explicit preparation and activation commands

These commands document the lower-level preparation interface used by the
scripted Atlas initialization above. Do not repeat fresh preparation or import
against the established authorities; use `atlas-infra.py start` for normal
startup. Never source a production `.env` while
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

Build and prepare code only after restoring the external volumes. The scripted
initializer installs the host receive dispatcher and startup checks it;
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

The corresponding low-level OpenVox-only startup and health commands are:

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

Atlas's production runtime now starts and passes service health checks.
Native-agent enrollment and actual host Docker-role application passed on
2026-10-19; a second enrollment-helper run also passed without changing the
native, website, IPA or SSH identities.
Independent authority restoration, isolated host reboot and image rollback
passed the maintenance acceptance above.

## Separate opt-in Vault configuration

`docker/openvox/infra-vault.override.yaml` is the explicit Vault configuration
used by Atlas's initializer and normal startup. It is never included by the
OpenVox profile automatically and leaves the legacy
`docker/vault/config/vault-conf.hcl` unchanged. The override
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

For a low-level Vault-only start with established state and certificates,
select only Vault on the intended host:

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
