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

## Certificate-auth CRLs: observed existing behavior

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

These are observations of existing Vault behavior, not ML-DSA-specific defects.
The custom validating publisher, short CRL lifetimes and freshness enforcement
were removed at the user's request. This deployment keeps the pre-existing
behavior and does not add a CRL distribution service or closure gate.

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

## Outbound clients and scope

The existing Python autosigner and Hiera HTTPS clients verify certificate trust,
hostname and validity. They do not gain custom CRL enforcement in this migration.
The Hiera fork retains ML-DSA certificate authentication and a small fix copying
the server's configured TLS protocol list into its Java HTTP client. The CRL
option and custom validation adapters have been removed.

The accepted classical Vault TLS fallback and verified internal PKI seed storage
remain as described above. Fleet compatibility, independent recovery and final
released-artifact acceptance are tracked in the
[rehearsal report](openvox-rehearsal-report.md).
