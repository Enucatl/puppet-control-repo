# Disposable OpenVox rehearsal (#12)

This rehearsal runs on the local Docker daemon, independently of production
`docker.home.arpa`. It publishes no ports, attaches no external networks and
loads no production `.env` files. The root Vault has no network interface other
than loopback. OpenVox, online Vault and the test agent share an internal Docker
network; a separate deployment helper downloads modules over an egress network.

Run from the control-repo checkout:

```sh
docker build -t openvox-rehearsal-tools:24001 docker/openvox
python3 scripts/openvox-rehearsal.py run
python3 scripts/openvox-rehearsal.py status
python3 scripts/openvox-rehearsal.py stop
python3 scripts/openvox-rehearsal.py up
```

After a complete run, the original project is stopped and the independent
`openvox-rehearsal-restore` project is running from restored volumes. Inspect or
manage it with `status --restored`, `stop --restored`, `up --restored`, or
`destroy --restored`. Both offline-root containers remain stopped. A repeated
full run refuses to overwrite an existing restore project; destroy that test
project before rerunning. Each run keeps its own protected recovery archive.

`run` explicitly initializes fresh disposable identities or uses the existing
rehearsal volumes. `up` only starts and unseals established state: missing
identities or storage fail instead of generating a replacement authority. The
packaged OpenVox entrypoint also rejects an absent server/CA identity. `stop`
keeps state. To remove this project's containers, networks and volumes:

```sh
python3 scripts/openvox-rehearsal.py destroy
python3 scripts/openvox-rehearsal.py destroy --restored
```

Destroying the restored project also removes its generated configuration copy.
Protected archives and diagnostic logs remain in the local work directory.

The controller uses `/var/run/docker.sock` on this machine and refuses a daemon
whose name differs from the local hostname. Containers receive no Docker socket.
Only the `openvox-rehearsal` and `openvox-rehearsal-restore` projects are managed.
The controller stages a sanitized Git snapshot under
`/tmp/openvox-rehearsal-<uid>/repo`, because this installation's
user namespaces cannot read the NFS-backed checkout. It includes current
Puppetfile/helpers and an explicit allowlist of rehearsal files, excluding `.env`
and untracked private state. It does not alter the source repository's history.

## Hierarchy and initialization

The derived tools image pins the accepted `24001` OpenVox image and Vault 2.2.0
by digest. Git and r10k are integration tools; the accepted OpenVox packages and
provider policy remain image-owned.

Initialization creates an offline Vault root and separate OpenVox and FreeIPA
intermediates. The OpenVox intermediate key stays in OpenVox's volume; the
FreeIPA intermediate stays in online Vault. Only public certificates/CRLs move
out of the offline authority. Vault control APIs use TLS 1.3 from the first
request, initially with locally pinned, ML-DSA self-signed transport identities
(not CA authorities). Online Vault then uses its intermediate-issued transport
identity. The offline-root service stops before enrollment.

The server has prepared ML-DSA private/public host keys and imports the externally
signed intermediate using the packaged CA CLI. Its listener serves the complete
chain. Server and agent configurations explicitly set `key_type=mldsa44` before
enrollment. OpenSSL policy enforces seed-only key serialization, TLS 1.3 and
`X25519MLKEM768`; it disables earlier protocols independently of Puppet's minimum
protocol override. JVM group/signature policy is supplied before startup, and
both listener and outbound protocols are restricted to TLS 1.3.

The accepted CA CLI uses `ca_ttl` for its initially imported server certificate
and does not cap that calculation to the issuer. The rehearsal sets it to one
day and refuses import unless the fresh issuer has more than a day remaining.
Runtime verification independently checks the actual signed lifetimes. Existing
test state with an overlong server leaf was corrected through CA renewal using
the same key, then the complete listener chain was restored and rechecked.

Volumes persist CA, SSL, code, bare Git/hooks, r10k cache, mutable server data and
operator JRuby gems separately. They do not mask packaged provider policy or
gems. New volumes are writable by UID/GID 999 inside Docker's user namespace.
The disposable Vault uses a single unseal share kept with its local recovery
state; this test arrangement is not the independently protected offline recovery
procedure required for production.

## What the test exercises

The deployment helper archives the real control repo and installs its modules,
including `vault_secrets` from `feature/mldsa44`. It overlays a safe manifest that
only manages `/tmp/openvox-rehearsal-marker`; production infrastructure profiles
are not applied. Both production/dev environments are deployed through r10k and
a bare Git post-receive hook, with type generation and mTLS environment-cache
invalidation. Hiera uses the actual Vault module. ENC and autosigner are the real
repo scripts, with test Vault routing/trust and certname-bound single-use tokens.

Sanitized checks cover catalog convergence, actual handshake protocol/group and
both CertificateVerify algorithms, malformed/wrong identities, TLS downgrade
and classical-only peers, token replay denial, least-privilege Vault login and
issuance, seed encodings, scheduled runs, renewal/revocation/replacement, server
recreation and online Vault restart. Recovery archives all eleven volumes and
both exact images, reloads the image archive locally, and restores the hierarchy
and agent into the separate project before checking convergence. Configuration,
helpers and the controller are also archived and restored into a separate local
tree; restored containers do not depend on the original staging directory.
This proves a local isolated restore; it does not establish independent off-host
backups. Raw
diagnostics and test credentials are never included in the public report.
Results are in `/tmp/openvox-rehearsal-<uid>/results.json`; failure logs are in its
mode-0700 `private` directory. Treat runtime volumes and private logs as secret.

## Open readiness gates

Vault 2.2.0 has no listener curve allowlist. Its TLS 1.3 listener may accept a
classical-only client. The user accepted that temporary Vault exception: capable
clients must prefer and negotiate `X25519MLKEM768`, while Vault may also serve
classical-only peers. A dedicated probe offers both `X25519MLKEM768` and `X25519`
and must negotiate the hybrid group. Classical-only Vault acceptance is recorded
as an observation rather than a blocker. The configured native/Java clients
currently require the hybrid group, which is stronger than the preference
requirement; OpenVox still rejects classical-only peers. No Vault fork, TLS
proxy, or new PKI is required under this accepted policy.

Additional follow-up checks now cover actual Python/JRuby outbound negotiation
and certificate/hostname rejection, original internal Vault PKI seed encodings, offline-root
renewal/revocation/publication, and fresh production-config PKI import. Run the
outbound test on an established disposable stack with the pinned ML-DSA Vault Hiera
module already deployed:

```sh
python3 scripts/openvox-rehearsal.py transports --restored
```

It temporarily replaces the safe fixture's Hiera URI with controlled TLS peers,
then restores the file and invalidates its environment cache, including on
failure. The result is `outbound-transports.json`, separate from the original
full lifecycle evidence. The 15 outbound checks cover existing TLS policy;
custom outbound CRL enforcement is no longer required. Puppetfile pins the
published Hiera transport and protocol-setting fix.

The prepared [infra profile](openvox-infra.md),
[offline-root procedure](openvox-offline-root.md),
[Vault limits](openvox-vault-limitations.md) and
[BCJSSE audit](openvox-jvm-policy.md) document the next deployment boundaries.
Final native Ubuntu/Debian, hardened Compose, hosted CI, clean protected off-host
recovery, Docker-host reboot and image rollback acceptance passed with release
24001. See the [final report](openvox-rehearsal-report.md) for evidence.
Full workload application/provisioning remains #15, FreeIPA consumers #14/#8,
production recovery custody #11 and Atlas execution #18. Production cutover is
#13. Broader BCJSSE conditional-policy compatibility is not claimed.

The sandbox uses short-lived certificates (one day), so retained test services
will expire unless renewed. It is intended for disposable verification rather
than unattended operation.
