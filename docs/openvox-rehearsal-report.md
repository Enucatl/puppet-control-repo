# OpenVox #12 disposable implementation report

## Review follow-up in the isolated dev checkout

Follow-up work uses `/home/user/code/puppet-control-repo`, a fresh GitHub-only
clone on `dev`, advanced to the current GitHub production baseline before
transferring the reviewed changes. The prior clean local checkout was preserved
at `/home/user/code/puppet-control-repo.before-issue12-review`. The original
NFS checkout and production infrastructure were not modified.

The real Hiera configuration now selects the container's root and Vault CRL
bundle and fails on lookup errors. Host enrollment rejects incompatible trust
and existing identities before token issuance or host mutations. Base-template
cloud-init now stops on installer failure. All 50 provisioning tests and five
Compose/Hiera wiring checks pass, along with relevant formatting/lint/syntax
checks.

A clean temporary Hiera Git candidate, `4edaac9b72b68d81e39bb40b8250654cecf03239`,
passed 254 unit examples, 20 runtime checks and all 17 outbound transport checks.
r10k fetched that commit into both test environments without runtime source
overlays. Actual Vault catalog convergence and renewal/revocation/replacement
also passed. Production Hiera options were tested with isolated DNS/CRL paths
and safe fixture manifests; native full-role catalogs were not run.
Evidence is retained at `/tmp/openvox-hiera-review/summary.json`. All services
in that unique test project are stopped; its diagnostic volumes are retained.

The temporary candidate was not published. The reviewed Hiera fix has since
been published as `9abf00d0ef0be71cf65fb97cf38eb9103da17fa6` and is now pinned
in `Puppetfile`; fresh acceptance of that published source is in progress. No actual repository commits/pushes,
hosted CI, reboot or independent recovery were performed in this follow-up.
The [CRL automation](openvox-infra.md#crl-automation) now implements signed
publication, rollback protection, Vault/OpenVox synchronization, native client
timers and failure/expiry checks. It remains opt-in and requires the matching
CA/library rebuild before deployment. The sections and recorded hashes below describe the earlier run.

### Vault-native CRL automation follow-up

The clarified requirement keeps the root private key inside Vault while Vault
remains available. Routine generation now uses the existing Vault PKI worker
for both root and intermediate CRLs: 72-hour validity and a 71h55m rebuild grace
period, so a rebuild becomes due about five minutes after generation. There is
no operator signing ceremony, custom rotation scheduler or publisher permission
to rotate either CA. The historical physically offline rehearsal below remains
evidence of the earlier design, not a production operational requirement.

Removed the separate public mirror, its routing/DNS requirement and both new
publication volumes. Native clients fetch the existing Vault/OpenVox public
endpoints directly. The remaining five-minute jobs verify pinned signatures,
chain completeness, expiry and persisted CRL numbers, install bundles atomically,
and synchronize consumers. A root revocation still reaches clients when the
revoked issuer is unavailable or malicious, using valid cached issuer evidence.
Native enrollment checks the full chain; the privileged Vault helper verifies
issuer/root CRLs before administrator-token requests.

The one-time authenticated setup helper configures Vault's workers and a
restricted AppRole. Each publication obtains a fresh short-lived batch token;
there is no expiring saved publisher token or token-renewal service. The host
job refreshes its protected client certificate/key copies from the native
agent's automatically maintained identity. An exact-path OpenVox authorization
rule grants that identity only root-CRL import, preserving packaged rules.
Puppet startup requests a refresh without a hard dependency that could strand
the daemon after a temporary boot-time network failure. Strict TLS revocation
checks and independent refresh retries remain in place.
No CA private key is exported for any of these operations. Healthy Vault still
receives a fresh root revocation when OpenVox is revoked or unreachable, so its
certificate-auth backend does not retain trust merely because OpenVox is down.

The owning CA changes remain in the local `openvox-server` and `jvm-ssl-utils`
forks under `/home/user/code/openvox-server`. The optional 72-hour CRL setting
bounds initial, revoked and renewed CRLs by issuer expiry; startup normalizes
imported long lists. Four CA tests with 82 assertions and RSA/ML-DSA library
checks passed. The accepted image still needs rebuilding.

The fresh isolated same-Vault fixture proved native background rebuilding:
a fresh signed list was available after the previous CRL expired, without any
rotation request. A real short-lived publisher token expired and was rejected;
the next run obtained a fresh login and succeeded. Direct public downloads,
scoped API denials, unchanged authority identities, root updates, revocation
denial, replacement enrollment and real Hiera catalogs passed. The delivered
setup helper installed protected credentials, preserved them on repeat and
successfully drove publication. Sanitized current evidence is in
[openvox-crl-results.json](openvox-crl-results.json).

The updater's 44 accepted-runtime scenarios include real ML-DSA HTTPS proving
that invalid/revoked peers receive no credentials, plus tampering, rollback,
crash barriers and malicious-issuer fallback. Host Ruby lacks ML-DSA support;
those tests run in the accepted container. The broader Python regression suite
passed 79 checks; the ten existing ML-DSA CRL tests passed separately as well.
Compose/catalog/setup/authorization tests are recorded in the evidence file.

Live CA testing used a temporary AOT CA/library overlay before the accepted JAR;
it is not release-image acceptance. Deployment still requires rebuilt/published/
pinned artifacts, authenticated initial provisioning, explicit activation and
verified operational alerts. Vault restart/unseal trust for the new hierarchy
also remains a cutover check. No repository push, native fleet activation, host
reboot, hosted CI or independent recovery was performed.

The disposable rehearsal passed on `forbearance` on 18 October 2026, with
additional implementation and acceptance checks on 19 October. The complete
run exited 0. Consolidated verification covers restored runtime, normal
management, TLS/authorization and accepted-image rollback, alongside the focused
Python test suite. Vault's acceptance of classical-only peers is now an accepted
temporary exception and an observation, rather than a failed readiness gate.

**This implements the isolated rehearsal, not a production-ready closure of
issue #12.** Production services on `docker.home.arpa`, production volumes,
routing and DNS were not changed. The source checkout is on that host's NFS
share; its requested source changes were made there, while containers used local
staged inputs and local Docker volumes. Existing unrelated checkout edits were
preserved. Nothing was committed, pushed, or posted to GitHub.

## Implementation changes

| Files | Change |
| --- | --- |
| `docker/openvox/Dockerfile` | Pins the accepted OpenVox and Vault images; adds Git and r10k 5.0.3; prepares nonroot-owned volume destinations. |
| `docker/openvox/compose.yaml` | Separate rehearsal project, no published ports, internal runtime network, offline-root namespace, separate deployment egress, nonroot startup, readiness, graceful shutdown and memory/PID limits. |
| `root.hcl`, `vault.hcl`, `vault-api.py`, `transport.rb` | TLS-only local control APIs, explicitly initialized persistent Vault state, pinned initial transport identities, separate offline/online authority state. |
| `puppet.conf`, `agent.conf`, `ca.conf`, `webserver.conf`, `rehearsal.conf` | Explicit ML-DSA keys before enrollment, complete server chain, bounded certificate TTLs, real ENC/autosigner, writable persisted client state and TLS 1.3 configuration. |
| `openssl.cnf` | Seed-only private-key encodings, hybrid-only native groups and protocol restrictions that survive Puppet's minimum-version override. |
| `pki.rb`, `ready.rb`, `check.rb` | Fresh intermediate/server keys and external-CA import preparation, issuer-lifetime guard, verified readiness and lifecycle requests. |
| `deploy.sh`, `fixture/hiera.yaml`, `fixture/manifests/site.pp` | Sanitized real control repo, isolated bare Git and r10k production/dev environments, type generation, authenticated cache invalidation and a safe Vault-backed marker catalog. |
| `acceptance.rb`, `tls-evidence.rb`, `verify-crypto.rb`, `verify-state.rb` | Actual handshake evidence, positive/negative authorization checks, original PKCS#8 seed checks, certificate/CRL signatures, constraints, lifetime bounds and runtime root-key absence checks. |
| `scripts/openvox-rehearsal.py` | Explicit initialize/run versus identity-preserving management commands; sanitized local staging; enrollment, scheduled runs, renewal, revocation, replacement and public evidence. |
| `scripts/openvox_rehearsal_recovery.py` | Private checksummed archives of all 11 volumes, both images and configuration/helpers; local image reload; separate restored configuration/project; fingerprint and convergence checks. |
| `Puppetfile` | Selects the Vault module's `feature/mldsa44` branch. Actual deployed revision is recorded in evidence. |
| `scripts/autosign.py` | Reads `VAULT_ADDR` and `VAULT_CACERT` when invoked, with explicit CLI options taking precedence. Existing name/signature/token restrictions remain. |
| `.github/workflows/puppet.yaml` | Replaces stock Puppet 8 with the accepted signed Ubuntu 24.04 OpenVox agent; checks archive fingerprint and OS preferences; adds rehearsal lint/syntax/tests and recovery self-check. |
| `tests/test_autosign.py`, `tests/test_openvox_rehearsal.py` | ML-DSA CSR acceptance/rejection, trust overrides, isolation, secret-safe diagnostics, binary staging, lifecycle management and issuer-bound import regressions. |
| `docs/openvox-rehearsal.md`, this report and the adjacent results JSON | Run instructions, implementation boundaries, findings and sanitized evidence. |

All abbreviated runtime filenames in the table are under `docker/openvox/`.
The existing production Compose stack now has a separate opt-in OpenVox
profile. Legacy PKI defaults remain unchanged for the existing installation;
explicit `--offline-root` paths select the new preparation boundary. No service
was activated on production.

## Verification completed

- A fresh offline Vault root signed separate OpenVox and FreeIPA intermediates.
  The online Vault holds the FreeIPA intermediate; OpenVox holds its intermediate.
  The root service stopped before enrollment and stayed outside runtime networks.
- Actual server/bundled-agent/client configuration reported `mldsa44`.
- The real Python autosigner enrolled the native agent using an orphaned,
  certname-bound single-use token. Replay produced an actual Vault 403.
- The real ENC, Vault Hiera function and native agent applied the safe catalog,
  submitted facts/reports, converged, and completed scheduled runs.
- Git/r10k deployed both environments, generated types, invalidated server caches
  through mTLS and exposed a changed Hiera value in the next catalog.
- Fresh native-agent and direct mTLS handshakes negotiated TLS 1.3,
  `X25519MLKEM768`, ML-DSA-44 in both CertificateVerify directions and
  `TLS_AES_128_GCM_SHA256`. Wrong CA/hostname and OpenVox TLS 1.2/classical-only
  peers were denied. Token bootstrap remained server-authenticated TLS.
- Vault acceptance requires hybrid negotiation when a client offers both
  `X25519MLKEM768` and `X25519`. Its classical-only fallback is recorded separately
  under the accepted temporary policy; configured native/Java clients continue
  to require the hybrid group.
- Agent Vault login/own-namespace service issuance succeeded; KV reads, another
  CN/SAN/IP, and another issuance role were denied. Actual persisted native and
  issued keys passed seed-only encoding checks.
- Renewal preserved the key and changed the certificate serial. Revocation
  signed/published the CRL, reloaded it, rejected the revoked identity at OpenVox
  and Vault, then allowed fresh-key replacement enrollment.
- Server recreation and Vault restart preserved established identity and access.
- The image archive reloaded without a registry fetch. Separate volumes and a
  separate configuration tree restored the same root, CA/server identities,
  Vault state and agent. Catalog apply/convergence and TLS/authorization/seed
  checks passed in the restored stack.
- Documented restored-stack stop/start commands preserved identities and agent
  convergence. Image rollback from accepted build 18001 to accepted build 13001,
  then back to 18001, preserved keys, certificates, seed encodings and effective
  configuration; the actual agent converged against both versions. The old image
  digest was independently checked against its public release provenance and
  registry manifest, rather than using a similarly named local build.
- The actual OpenVox 9 parser accepted the repository's profile and Proxmox
  workflow manifests. Ruff, shell/Ruby syntax, recovery self-check and
  `git diff --check` passed. The hosted GitHub workflow was not launched.

## Fixes and findings encountered

1. **NFS/user namespaces:** containers could not read binds from `/export/docker`.
   Staging now uses a local sanitized Git snapshot, excludes environment/private
   files, handles binary/Unicode content, removes stale files and preserves
   mounted directory/file inodes on updates. No NFS permission changes were made.
2. **Image contract:** the published image lacks Git, r10k and Vault tools, and
   new volume destinations needed UID/GID 999 ownership. A derived rehearsal
   image supplies those without masking packaged provider policy or gems.
3. **Packaged environment collision:** r10k refused the image's populated
   `production` directory. Rehearsal environments now have their own path.
4. **Git hooks:** empty templates omit `hooks/`; inherited Git environment variables
   affect nested Git calls; Git push can hide hook failure. The deployment script
   handles all three and verifies deployed revisions.
5. **CLI/configuration differences:** `puppet generate types --section` is invalid;
   nonroot Puppet needs an explicit configuration path; separate HOCON files
   cannot repeat the same settings. Calls and CA configuration were corrected.
6. **Agent persistence:** Puppet manages permissions on its CA file, so it cannot
   use a read-only trust mount directly. The authenticated bundle is copied to
   writable client SSL state. Independent restore also exposed a default public
   directory under ephemeral HOME; it now lives at `/agent/public`.
7. **Enrollment evidence:** successful enrollment removes the original CSR, and
   the Puppet wrapper removes `RUBYOPT`. Replay reconstructs a signed request
   carrying the consumed token in memory; instrumentation uses native Ruby
   directly. Credentials are cleaned in `finally`; only an actual Vault 403
   counts as successful replay denial.
8. **Native cryptography defaults:** generic OpenSSL serialization included
   expanded private material, and Puppet sets TLS minimum 1.0 internally. Provider
   seed-only policy and independent protocol disabling fixed both in this
   deployment; actual writers and negative handshakes were tested.
9. **CA CLI lifetime defect:** import used `ca_ttl` rather than `cert_ttl` and
   created a server leaf two seconds beyond its issuer. Existing state was
   renewed through the CA API using the same key. Fresh import now uses a one-day
   TTL plus an issuer-remaining-lifetime guard. The artifact's general uncapped
   calculation still needs an owning-fork fix; the verifier was not weakened.
10. **Recovery completeness:** restoration now includes independent configuration
    and image archives, not just volumes. Normal start refuses missing authority
    state, unseals existing Vault, restarts the agent, and preserves prior test
    evidence if a management command fails. Archives are mode 0600 in a mode-0700
    directory, with checksums and a free-space preflight.
11. **Existing development environment:** `.venv/bin/pytest` has a stale relocated
    shebang. Tests use `uv run --frozen python -m pytest`; the environment was not
    otherwise changed. Forge warned that `puppet-boolean` and
    `puppetlabs-resource_api` are deprecated; unrelated dependencies were retained.
12. **Accepted Vault transport exception:** the original strict rejection test
    recorded classical-only X25519 acceptance as a failed readiness gate. The
    user subsequently accepted that temporary listener behavior, provided capable
    clients prefer and negotiate `X25519MLKEM768`. Acceptance now probes a client
    offering both groups and records classical-only acceptance as an observation.
    Current native/Java clients still require hybrid negotiation, and OpenVox
    still rejects classical-only peers. No Vault fork, TLS proxy, or new PKI is
    needed under the accepted policy.

## Follow-up implementation and fixes (19 October)

- Added the opt-in `openvox` service to `docker/docker-compose.yml`, distinct
  `infra-*` configuration, nonroot/read-only runtime, eight external state
  volumes, a container Git/r10k hook and fail-closed readiness/startup. Built the
  actual derived infra image and passed nine image/tooling/preflight checks.
  Selecting it does not start Vault setup or initialize authorities.
- Added a separate manual Vault override and HCL template, pinned to Vault
  2.2.0 with TLS 1.3 bounds, full served chain, optional client certificates
  and no raw-storage endpoint. Seven checks passed with the exact HCL against
  isolated, uninitialized storage. It selected hybrid exchange when offered,
  retained the accepted classical fallback, rejected TLS 1.2 and did not
  require a certificate before token bootstrap. The active production Vault
  configuration was not modified.
- Added explicit `infra-pki.rb prepare/import` commands. A mandatory operator
  root fingerprint, ML-DSA seed-only key pairs, chain constraints, signatures,
  lifetime bounds and root CRL must validate before the packaged CA CLI imports
  fresh state. Eleven isolated checks passed, including wrong trust, short
  issuer, revoked intermediate and refusal to replace an existing authority.
- Added the offline-root ceremony script: signing, same-key intermediate
  renewal, serial revocation, signed root CRL refresh/export and guaranteed
  root shutdown. Nine lifecycle checks and three static-publication probes
  passed. No established intermediate was revoked. Independent root custody
  remains an operational requirement.
- Added explicit offline-root dispatch to legacy scripts 01–04. The online
  intermediate helper generates an ML-DSA CSR and imports only a pinned,
  externally signed chain. Five checks passed against an independent
  network-isolated TLS Vault. The old online-root/RSA/systemd defaults remain
  available only as the legacy path until #13.
- Audited **original decrypted logical PKI key records** from independent
  protected archive clones. Both Vault's internal root and intermediate are
  persisted as seed-only PKCS#8. Audit clones and volumes were removed.
- Added actual outbound peer tests through the Python autosigner and real
  JRuby Hiera catalog compiler. They exposed missing CRL verification in both
  clients. The autosigner now accepts `--crl`/`VAULT_CRL` and rejects plaintext
  Vault endpoints. The local Vault Hiera fork adds an explicit `crl` option for
  native and Java contexts. With those changes, all 17 transport checks pass:
  valid hybrid/ML-DSA connections and denial of classical-only, TLS 1.2,
  wrong-name, untrusted, expired and revoked peers. Token bootstrap correctly
  has no client certificate. The real Vault-backed catalog still compiles.
- A stricter TLS 1.2 probe exposed a second Hiera bug: its separately created
  Java HTTP client did not apply the server's configured protocol list. Global
  JVM protocol properties alone did not fix that path. The owning module now
  applies the existing server setting to its client options; acceptance requires
  a protocol-version rejection rather than counting a signature mismatch.
- The JRuby pool retained the old required class after module replacement;
  restarting the disposable server loaded the fix. Environment-cache flushing
  alone does not reload that class. The local fork passed 254 unit examples;
  its four changed files remain unpublished, so this dependency is a readiness
  gate for fresh remote deployments.
- Added a shared signed OpenVox installer to every current native enrollment
  caller: Proxmox hosts, desktop, Docker and base/FreeIPA acceptance templates.
  It verifies the archive fingerprint, OS preference and exact version, applies
  seed-only/TLS 1.3/hybrid policy and explicit `mldsa44`, and leaves the service
  masked until enrollment. Public trust is supplied through `OPENVOX_CA_CERT`;
  different existing trust and non-ML-DSA keys require explicit migration.
- Full cloud-init rendering exposed unsafe token/server interpolation in shell
  and YAML. JSON argument arrays and CSR attributes now retain literal values;
  credential files are restricted before writing. Tests cover full templates,
  wrong archive keys, unsupported platforms, mismatched packages and RSA refusal.
- Reconciled README, enrollment and migration documentation with the fresh-chain
  decision. Expanded CI to cover the new paths and use a pinned Compose baseline.
- Added fail-closed CRL publisher validation before the Vault API upload. The
  exact bytes being uploaded must match a trusted issuer and pass ML-DSA
  signature/freshness checks. Ten live checks rejected tampering, unrelated
  signers/issuers, stale/future/incomplete CRLs, classical signatures and empty
  input while preserving the current CA/CRL files. Vault itself still lacks
  those validations.

The expanded local Python suite passed **294 tests**. Ten additional CRL
regressions require ML-DSA-capable Ruby: the host's older Ruby skips them, while
all ten passed with the accepted container Ruby. CI runs them after installing
the accepted native agent. Ruff, Ruby/shell syntax, recovery self-check and
diff checks passed; hosted CI has not run.

The final recreated stack preserved authority/server identities, loaded the
new CRL environment, applied and converged its real Vault-backed catalog, and
passed all 20 runtime acceptance checks plus all 17 outbound transport checks.
Fresh rehearsal and restore commands now include those outbound checks as
mandatory gates; a missing deployed Hiera fix fails rather than waiving CRLs.

The dedicated [Vault limitations](openvox-vault-limitations.md) document explains
why listener hybrid-only enforcement cannot be configured, why cert-auth CRLs
need publisher validation, and why raw issuer-DN equality matters. The
[JVM policy audit](openvox-jvm-policy.md) reproduced BCJSSE's unsupported
conditional expressions while preserving JDK defaults. Ten provider checks
passed; this does not claim general conditional-policy support.

## Remaining gates

- Publish the local Vault Hiera CRL/protocol fix and record the deployed resolved
  revision. The restored test server currently uses an explicit local overlay;
  a fresh r10k fetch of the remote branch does not contain that fix yet.
- BCJSSE still ignores conditional JDK `disabledAlgorithms` expressions.
  ML-DSA-only certificate/handshake signature lists, TLS 1.3, hybrid-only groups
  and pinned private trust compensate in this controlled profile. Broader
  provider semantics and every affected JVM consumer are not established.
- The CA library's generated CRL lifetime defaults to five years, beyond this
  rehearsal intermediate's lifetime. Publisher checks validate current
  issuer/signature/freshness, but do not claim to bound CRL `nextUpdate` by issuer
  expiry. This is an owning CA-library limit, separate from Vault.
- The initial native platform inventory maps to accepted Ubuntu 24.04 and
  Debian 13 amd64 targets, with Ubuntu 26.04 accepted for replacements. Actual
  disposable native guests and complete production role catalogs remain to be
  exercised. Host installation was not performed.
- Configure and test real public issuer/CRL routes, refresh monitoring and
  independently protected updated root/state backups. The rehearsal's issuer
  URL lists remain empty, and one-share local recovery is not independent media.
- Full infra service activation, host reboot, cold bootstrap without the old
  services, hosted CI, legacy-system rollback and independent Atlas recovery
  remain unverified. #13 owns live enrollment/cutover, #14/#8 actual CA-less
  FreeIPA compatibility, and #18 the later Atlas execution.

## Retained environment

Three containers remain running in `openvox-rehearsal-restore`: OpenVox, online
Vault and the test agent. The original stack and both offline-root containers
are stopped. There are no published host ports. Use:

```sh
python3 scripts/openvox-rehearsal.py status --restored
python3 scripts/openvox-rehearsal.py stop --restored
python3 scripts/openvox-rehearsal.py up --restored
```

The accepted image is `9.1.0-mldsa.18001`, digest
`sha256:cbba872ef4b71865a8f7aacf395e4ff4ec28865995c5a9cec8023afe9d15b185`.
The derived tools image ID is
`sha256:61ec01b380dbe7e7fb9e7803a2fb04efdb1dc5045bbe9d7b5b2f35de47fb547c`.
Actual runtimes: Java 25.0.4.1, Ruby 4.0.7/OpenSSL 3.5.9, Python 3.14.4 with
SSL 3.5.5/cryptography 50.0.2, and Vault 2.2.0. The installed Vault module revision
is `1690516e1e0a4d575ee532ef10af2e585a85baa3`.

The previous accepted image digest is
`sha256:38a0923732977a0eae63305d2c53cb91b18321bf26142814ab916ff0524d300b`.
It is retained locally for rollback tests; the running server uses build 18001.

The protected recovery archive is approximately 500 MiB. Root filesystem free
space after testing was approximately 2.6 GiB. Certificates are deliberately
short-lived (one day); this sandbox is not configured for unattended operation.
Detailed sanitized evidence is in [openvox-rehearsal-results.json](openvox-rehearsal-results.json).
Raw credentials/keys and private diagnostics are excluded from that file.
