# Vault configuration limits for issue #12

Verified on 2026-10-18 against the local disposable rehearsal, Vault **2.2.0**,
commit `2891bfd23893ebd302307050edaf03d871ea8351`, image digest
`sha256:e0acd3c681bdf57f002d7214e5d050b876e1704678005c6b0406dd96a65be919`.
All runtime work used the local Docker socket. Production services on
`docker.home.arpa` were untouched.

## TLS key exchange: hybrid preference works; server enforcement is unavailable

The restored Vault selected `X25519MLKEM768` when the client offered
`X25519MLKEM768:X25519`. A classical-only client also completed TLS 1.3 with
`X25519`. Both connections used ML-DSA-44 client/server signatures. These are
separate properties: post-quantum certificate signatures do not make classical
X25519 key exchange post-quantum.

Vault's listener configuration exposes protocol bounds and cipher suites, but
no key-exchange group allowlist. Its TLS builder leaves Go's `CurvePreferences`
unset. `tls_cipher_suites` cannot express a TLS 1.3 key-exchange policy.
Sources: [listener configuration fields](https://github.com/hashicorp/vault/blob/2891bfd23893ebd302307050edaf03d871ea8351/internalshared/configutil/listener.go#L87-L105),
[TLS configuration construction](https://github.com/hashicorp/vault/blob/2891bfd23893ebd302307050edaf03d871ea8351/internalshared/listenerutil/listener.go#L108-L166),
[Go group configuration](https://github.com/golang/go/blob/go1.27.2/src/crypto/tls/common.go#L807-L821).

The operator accepted this temporary Vault fallback. Keep both listener bounds
at `tls13`; configure capable clients for `X25519MLKEM768` and check negotiated
groups after upgrades. The rehearsal's native/JVM clients currently require the
hybrid group, which also satisfies the preference requirement. OpenVox retains
its stricter rejection policy. No Vault fork, TLS proxy, or PKI replacement is
required for the accepted policy. Requiring Vault itself to reject classical
clients would need additional implementation; this release has no HCL setting
for it. [Upstream request](https://github.com/hashicorp/vault/issues/32084).

## Internal ML-DSA storage: seed-only encoding is now verified

Both internal PKI keys passed a direct storage audit:

| Authority | Mount | Original stored PKCS#8 payload | Result |
| --- | --- | --- | --- |
| Offline root | `pki/` | context tag `0x80`, length 32, 32-byte seed | Pass |
| Online intermediate | `pki_int/` | context tag `0x80`, length 32, 32-byte seed | Pass |

The audit restored the existing protected root/Vault recovery archives into
independent temporary volumes. Each clone ran with `--network none`, no
published ports, and `raw_storage_endpoint = true` only in its temporary
configuration. After unsealing the clone, the audit read
`sys/raw/logical/<mount-uuid>/config/key/<key-id>`, decoded the logical JSON
record, and parsed the **original** `private_key` PEM/DER. It checked the
ML-DSA-44 OID and exact seed-only payload before loading the key. It never
converted an expanded encoding into a seed encoding before checking it.
Both clones and temporary volumes were removed. Recovery keys, root tokens and
private PEM bytes were not printed or included in public evidence.

This closes the earlier uncertainty about the stored PKI key representation;
it supplements the existing exported/native-key checks. It does not establish
that every unrelated Vault secret or barrier key uses this encoding.

The implementation agrees: Vault generates ML-DSA keys through
`x509.MarshalPKCS8PrivateKey`, stores the PEM in `KeyEntry.PrivateKey`, and writes
that JSON through logical storage. Go's ML-DSA PKCS#8 writer uses the seed
choice. Sources: [generation](https://github.com/hashicorp/vault/blob/2891bfd23893ebd302307050edaf03d871ea8351/sdk/helper/certutil/helpers.go#L437-L465),
[key record and storage](https://github.com/hashicorp/vault/blob/2891bfd23893ebd302307050edaf03d871ea8351/builtin/logical/pki/issuing/keys.go#L28-L87),
[Go PKCS#8 writer](https://github.com/golang/go/blob/go1.27.2/src/crypto/x509/pkcs8.go#L184-L194).

The sanitized runtime evidence is
`/tmp/openvox-rehearsal-175200003/vault-storage-audit.json`, including archive
SHA-256 hashes and clone cleanup confirmation. The normal root/Vault
configuration keeps the raw storage endpoint disabled.

## Certificate-auth CRLs: Vault needs a trusted validating publisher

Vault's cert-auth CRL API explicitly ignores an uploaded CRL's expiration.
Its upload path parses the CRL and stores revoked serial numbers without
verifying the signature or associating entries with an issuer. Login compares
chain certificate serials against all CRLs in that cert-auth mount. An unrelated
issuer's matching serial can therefore reject an otherwise valid client.
Sources: [expiry contract and upload](https://github.com/hashicorp/vault/blob/2891bfd23893ebd302307050edaf03d871ea8351/builtin/credential/cert/path_crls.go#L65-L68),
[serial-only storage](https://github.com/hashicorp/vault/blob/2891bfd23893ebd302307050edaf03d871ea8351/builtin/credential/cert/path_crls.go#L276-L318),
[serial lookup](https://github.com/hashicorp/vault/blob/2891bfd23893ebd302307050edaf03d871ea8351/builtin/credential/cert/path_crls.go#L139-L152),
[chain revocation check](https://github.com/hashicorp/vault/blob/2891bfd23893ebd302307050edaf03d871ea8351/builtin/credential/cert/path_login.go#L932-L952).

This was also reproduced on the isolated online clone. A newly issued ML-DSA
client authenticated successfully. An expired CRL, signed by a fresh unrelated
ML-DSA key and naming another issuer, was accepted. Its colliding serial then
caused that client's login to fail. This required privileged CRL-write access;
it was not an unauthenticated modification of revocation state.

For this deployment, the publication path must authenticate the issuer, verify
the CRL signature and validity interval, then publish through tightly scoped
credentials. Monitor freshness outside cert auth and replace stale lists with
fresh authoritative lists. Do not treat uploading a CRL as evidence that Vault
validated its provenance. Separate cert-auth mounts are an available boundary
if independent issuers need independent serial-number namespaces. No cert-auth
HCL/API switch in this version enables issuer-scoped matching or expiry-based
login rejection.

URL-backed CRLs use `NextUpdate` to schedule periodic fetches. On a failed
refresh, the existing serial list remains; the login path has no CRL freshness
check. This is source-level evidence, not a simulated network-outage test.
Source: [fetch and periodic refresh](https://github.com/hashicorp/vault/blob/2891bfd23893ebd302307050edaf03d871ea8351/builtin/credential/cert/backend.go#L187-L219).

The rehearsal publisher now validates the exact CRL bytes before uploading:
`verify-crypto.rb --crl PATH --issuer CA_BUNDLE` checks encoded issuer identity,
ML-DSA signature/key and current validity. Ten runtime checks passed, including
tampered, wrong-signer, wrong-issuer and expired CRLs. This compensates at upload;
it does not make Vault reject a CRL that becomes stale later.

An additional limit belongs to the CA library rather than Vault: regenerated
CRLs default to five years, longer than the disposable intermediate's lifetime.
The publisher does not claim to cap `nextUpdate` by issuer expiry. Source:
[CA CRL builder](https://github.com/Enucatl/jvm-ssl-utils/blob/bff2e9b99a1bc4280d5b5f346de29a6b33296a88/src/java/com/puppetlabs/ssl_utils/SSLUtils.java#L406-L415).

## Issuer distinguished names: textual equality is insufficient

For CA-backed certificate auth, Vault delegates chain construction to Go
`x509.Verify`. Go indexes candidate parents by their encoded raw subject and
looks them up using the child's encoded raw issuer. PrintableString and
UTF8String forms of an otherwise identical CN can therefore fail to chain.
Sources: [Vault verification](https://github.com/hashicorp/vault/blob/2891bfd23893ebd302307050edaf03d871ea8351/builtin/credential/cert/path_login.go#L978-L1005),
[Go parent lookup](https://github.com/golang/go/blob/go1.27.2/src/crypto/x509/cert_pool.go#L137-L171).

The earlier [issuer-encoding investigation](05-vault-cert-auth-puppet-issuer-encoding.md)
documents this failure on the old CA. The disposable new chain already passed
CA-backed Vault cert auth. Preserve the exact-DER gate
`leaf.RawIssuer == issuer.RawSubject` for future CA imports and signer changes;
do not assume a matching printed CN proves compatibility.

Vault's `use_csr_values` copies parsed CSR names into the certificate template,
not the original `RawSubject` encoding, so it is not a general way to preserve
CSR DN bytes. A UTF8-forcing subject name is a compatibility workaround for
signers that reconstruct names, not a replacement for testing the actual leaf.
Source: [CSR subject handling](https://github.com/hashicorp/vault/blob/2891bfd23893ebd302307050edaf03d871ea8351/sdk/helper/certutil/helpers.go#L1421-L1457).

## Outbound client revocation: fixed locally, publication still required

The first controlled-peer run exposed a separate client bug: the real Python
autosigner and Java/JRuby Hiera transport both accepted a revoked server
certificate. Trusting the CA did not automatically enable CRL checking.

The owning local fork `/home/user/code/openvox-server/vault-hiera` now supports
an opt-in Hiera `crl` PEM-bundle option. Native Ruby loads CRLs into the
certificate store and enables chain revocation flags. Java uses SSLUtils'
CRL-aware context builders for both token and certificate authentication.
The option requires verified HTTPS. The Python autosigner has the analogous
explicit CRL setting described in the deployment runbook.

Verification: **254 module unit examples pass**, including current/revoked/
expired/malformed/unrelated-signer CRL cases. The restored disposable server
uses only a backed-up overlay of the changed `vaultsession.rb` in its production
and dev modules. A local server restart was necessary because environment-cache
invalidation does not unload Ruby classes already required by the JRuby pool.
All **17 actual outbound transport checks** now pass: positive hybrid TLS 1.3,
classical/TLS 1.2 rejection, wrong hostname, untrusted issuer, expired certificate,
and revoked certificate. A catalog through the real Vault also returned HTTP
200 after restoring the normal Hiera configuration with its actual issuer CRLs.

The test fixture uses the OpenVox issuer's CRL plus the root CRL. The normal
Vault connection uses `/trust/vault-crls.pem`, containing the separate online
Vault intermediate CRL and root CRL. These are different issuer chains.

The Hiera fixes are now published at
`9abf00d0ef0be71cf65fb97cf38eb9103da17fa6`, and `Puppetfile` pins that immutable
revision. The clean r10k/release acceptance must still verify that exact published
source rather than a runtime overlay. Earlier overlay diagnostics remain at
`/tmp/openvox-rehearsal-175200003/vault-hiera-overlay.json`; private originals
and transport evidence are retained there for historical comparison.

## Remaining boundary

The accepted classical fallback is recorded as an observation, and the internal
PKI seed audit now passes. CRL expiry/signature/issuer handling remains a code
limitation in Vault itself. The opt-in [publisher](openvox-infra.md#crl-automation)
now verifies these properties before upload and alerts on stale/failed updates.
Vault performs routine CRL rebuilding internally, with unauthenticated downloads;
the synchronizer has no CA rotation permission.
Vault still does not enforce CRL expiry at certificate login. Full production wiring,
fleet compatibility, independent recovery and final released-artifact acceptance
remain tracked in the [rehearsal report](openvox-rehearsal-report.md); this audit
does not imply production readiness or a completed rollout.
