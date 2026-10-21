# Root custody and historical offline-root rehearsal

The root signing key remains inside Vault. The deployment retains existing CRL
behavior, without custom strict checks, lifetime settings or distribution jobs.
The notes below record an earlier network-isolated disposable experiment;
its publication and operator-ceremony steps are not #12 closure requirements.

The retired ceremony helper exercised the established network-isolated test
root, pinned its recorded SHA-256 fingerprint, and stopped it after each run.
It renewed one disposable intermediate with the same key, revoked the first
certificate, and verified both CRL signatures and rejection of the revoked
serial. No production authority or trust mount changed.

The rehearsal proved nine cryptographic/lifecycle checks. Its sanitized evidence
is `/tmp/openvox-rehearsal-175200003/offline-root-lifecycle.json`. Public artifacts
were written under
`/tmp/openvox-rehearsal-<uid>/public/offline-root/<operation-id>/`: the root
certificate, DER and PEM CRLs, both disposable intermediate certificates, and
`evidence.json`. Private root state and recovery material stayed in the protected
root volume. This one-share test recovery arrangement was not an operator
key-custody design.

A temporary HTTP server bound to host loopback served the public certificate and
DER CRL. A network-isolated container verified the fetched CRL signature and
rejected a bit-flipped CRL; those three checks are recorded as
`publication_probe`. A dedicated CRL mirror, scheduled publication ceremony and
CRL-expiry alarms are not deployment requirements. Normal CA code owns its
existing CRL behavior. The ceremony helper and its dedicated tests are retired;
these results remain historical evidence.

## Retained native intermediate experiment

The legacy numbered Vault setup scripts and their `--offline-root` dispatch
wrappers have been removed. The historical offline-root experiment retains its
native `docker/vault/scripts/offline-intermediate.rb` interface. It is separate
from Atlas's current Vault-held root and CA-less IPA bootstrap.

Use the accepted OpenVox native Ruby/OpenSSL runtime and explicit strict
`OPENSSL_CONF` policy. Supply a verified HTTPS `VAULT_ADDR`, administrator
`VAULT_TOKEN`, and readable `VAULT_CACERT` through the protected environment;
there is no bootstrap token-file fallback:

```sh
/opt/puppetlabs/puppet/bin/ruby docker/vault/scripts/offline-intermediate.rb \
  generate-csr --initialize /protected/freeipa-request
# Transfer intermediate.csr to the offline signer; transfer public signed files back.
/opt/puppetlabs/puppet/bin/ruby docker/vault/scripts/offline-intermediate.rb \
  import /protected/freeipa-request ROOT_SHA256
```

Generation requires the explicit `--initialize` flag, creates `pki_int` only if
absent, and refuses existing issuers, keys or local request directories. It never
calls a root-generation/signing endpoint. The private intermediate key remains
inside Vault; `intermediate.csr` and `request.json` preserve the request hash and
Vault key identifier. Protect and back up that request directory and the online
Vault state together. Missing state fails instead of creating a new authority.
Vault's internal key-encoding limitations are tracked separately in
[the Vault limitations report](openvox-vault-limitations.md).

Have the offline root sign that exact CSR for an uncompromised ML-DSA-44
intermediate with path length zero, a lifetime of at most the configured
720-hour mount cap, and more than 24 hours remaining at import. Place these
public artifacts beside the preserved request files:

- `signed.pem`: the signed intermediate followed by the pinned offline root.
- `root.pem`: that root certificate.

Import requires a mandatory root SHA-256 pin and validates both algorithms,
signatures, key/CSR correspondence, CA path lengths/key usage, issuer lifetime
bounds before changing Vault. Verified TLS 1.3 requests use the supplied trust
file before sending the administrator token. Plaintext URLs, skip-verification and TLS-name
overrides are rejected. The helper bypasses HTTP proxy environment settings.
Vault must still hold the original key identifier. A repeated import of exactly
the same certificate/key is read-only; a different current issuer fails and
requires an explicit renewal/recovery procedure. No key is deleted or replaced.

After import, configure the intermediate's publication URLs and scoped ML-DSA
leaf-issuance roles before use. This helper intentionally does not grant a broad
legacy `general` role or perform FreeIPA installation. Real CA-less consumer
compatibility and rollout remain #14 work.

A separate disposable, network-isolated TLS Vault demonstrated CSR creation,
offline signing, wrong-root-pin denial, successful import, unchanged repeated
import and refusal to regenerate an existing authority. It was removed after
the test; the running restored Vault's intermediate was not altered. Sanitized
evidence is `/tmp/openvox-rehearsal-175200003/legacy-offline-intermediate.json`.
During that test, an empty PKI mount returned HTTP 500 for `issuer/default`;
the helper now lists issuers first and queries the default only when one exists,
so it does not mask genuine HTTP 500 errors as a missing authority.
