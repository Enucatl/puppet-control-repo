# Root custody and historical offline-root rehearsal

The root signing key remains inside Vault. The deployment retains existing CRL
behavior, without custom strict checks, lifetime settings or distribution jobs.
The network-isolated procedure below records an earlier disposable experiment;
its publication and operator-ceremony steps are not #12 closure requirements.

`scripts/openvox_offline_root.py` exercises the established disposable offline
root on the local Docker installation. It never initializes a replacement root,
uses the explicit local socket and fixed `openvox-rehearsal` project, requires the
recorded root SHA-256 fingerprint, and refuses a root with Docker networking.
It stops the root in a `finally` block, including after a failed signing or
verification step. A stop failure is reported as a failed ceremony.

Run from the repository after the main rehearsal initialized the offline root:

```sh
python3 scripts/openvox_offline_root.py rehearse --expected-root-sha256 <recorded-root-certificate-sha256>
python3 scripts/openvox_offline_root.py publish --expected-root-sha256 <recorded-root-certificate-sha256>
```

Take the fingerprint from the recorded ceremony evidence, not from a new root
discovered during the operation. Existing protected Vault recovery inputs stay
inside the root volume and travel through private control requests; command
arguments and public output contain no tokens, unseal material or private keys.
This one-share test recovery arrangement is not an operator key-custody design.

`rehearse` performs these explicit ceremony steps:

1. Start and unseal the established network-isolated root and check its identity.
2. Generate a fresh ML-DSA-44 intermediate key and signed CSR in a new private
   directory within its offline volume. This disposable probe is separate from
   both current OpenVox and FreeIPA intermediates.
3. Sign that CSR for 48 hours with CA path length zero, then sign the same CSR for
   72 hours. Verify the renewal retains the public key, changes the serial and
   extends validity without exceeding the root's lifetime.
4. Revoke only the first certificate created by this invocation. Rotate the root
   CRL and verify its issuer, signature and validity. OpenSSL's certificate-store
   revocation check must reject the revoked certificate and accept its renewal.
5. Export public artifacts, stop the root and confirm it is stopped.

This tests serial revocation after routine renewal. A compromised intermediate
key requires revoking **every unexpired certificate for that key** and creating a
replacement key; retaining the key is appropriate only for an uncompromised
renewal. For a real ceremony, generate the intermediate key at its owning issuer
and transfer only its CSR to the offline root. The main rehearsal already uses
that separation for the actual OpenVox and FreeIPA intermediates.

`publish` rotates and verifies the root CRL, then exports it without issuing or
revoking any certificate. It does not merely recopy an aging CRL. Each operation
creates a new publication directory under
`/tmp/openvox-rehearsal-<uid>/public/offline-root/<operation-id>/`:

| Artifact | Purpose |
| --- | --- |
| `root.pem` | Pinned public root certificate |
| `root.crl` | Signed DER root CRL for a CRL distribution endpoint |
| `root.crl.pem` | The same signed root CRL in PEM form for local trust stores |
| `intermediate-initial.pem` | Disposable revoked intermediate (`rehearse` only) |
| `intermediate-renewed.pem` | Same-key renewed intermediate (`rehearse` only) |
| `evidence.json` | Sanitized verification results, issuer URLs and CRL expiry |

For the OpenVox trust volume, copy the exported **`root.crl.pem`** to
**`/trust/root.crl`**. That local import filename expects PEM; the exported
`root.crl` is DER and belongs on the public distribution endpoint.

Only these public files may cross from offline custody to a publication host.
Keep the root volume, private probe directories, unseal material and protected
backups out of any web-server document root. Back up the changed root storage
after signing or revocation, so recovery retains its issuance and revocation
history. Retain the older backup until the new protected copy is verified.

The live rehearsal proved nine cryptographic/lifecycle checks, including actual
ML-DSA-44 CRL verification and certificate-store denial of the revoked serial.
The latest sanitized result is
`/tmp/openvox-rehearsal-175200003/offline-root-lifecycle.json`.
Both `publish` and `rehearse` were run successfully; the current root certificate
fingerprint remained unchanged and the root is stopped.
An additional temporary HTTP server bound only to host loopback served the public
root certificate and DER CRL without credentials. A separate network-isolated
container verified the fetched CRL signature and rejected a bit-flipped CRL.
Those three checks are recorded as `publication_probe`; this proves the exported
bytes are usable through a static mirror, while the real publication route
remains a deployment requirement.

## Historical publication probe and remaining custody work

The current disposable root's `issuing_certificates`,
`crl_distribution_points` and `ocsp_servers` lists are empty. The command exports
verified publication files; it does not claim that fleet clients can discover
them or install them into the running restored stack. Existing clients use
explicitly supplied root certificate/CRL files. No production DNS, web server,
root issuer URL or current trust mount was changed.

The publication experiment above is historical evidence only. A dedicated CRL
mirror, scheduled publication ceremony and CRL-expiry alarms are not deployment
requirements. The normal CA code continues to own its existing CRL behavior.

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
