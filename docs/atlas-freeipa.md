# Atlas CA-less FreeIPA

This is the scripted implementation for [rehearsal #14](https://github.com/Enucatl/puppet-control-repo/issues/14)
and [deployment #8](https://github.com/Enucatl/puppet-control-repo/issues/8).
The final live import preserved all 10 users and 16 groups, including their
IDs, memberships, HBAC and sudo policies. Atlas is now enrolled through
OpenVox's `freeipa::client` resource against `ipa.home.arpa`; its full Docker
catalog passed. The existing `freeipa.home.arpa` service remains on Docker.
See [migration evidence](atlas-ipa-migration-results.json) and
[enrollment evidence](atlas-enrollment-results.json).

The realm remains `HOME.ARPA` and the domain remains `home.arpa`. The operator
confirmed that password Kerberos and SSH GSSAPI are sufficient: the installer
explicitly uses `--no-pkinit`. It supplies HTTP/LDAP leaves and CA trust directly,
without `--external-ca`, Dogtag installation, or a subordinate Dogtag CSR.

## Image

The image compatibility build lives in the
[FreeIPA container fork](https://github.com/Enucatl/freeipa-container/tree/mldsa44).
It derives from the pinned AlmaLinux 10 FreeIPA image without changing package
versions. Its patch reads SubjectPublicKeyInfo through the existing ASN.1 parser
and uses OpenSSL for HTTP certificate/key matching and encrypted key storage.
NSS retains full-chain and hostname validation. The build applies the patch with
zero fuzz against the pinned source.

The [Compose file](../docker/freeipa/atlas-compose.yaml) selects the published
image directly, with its native entrypoint. It owns the image pin, install
options, volume mount, healthcheck and startup instructions. The pinned image
comes from fork revision `cea6c6b250867cd9dbf23c7224d81d1ae7908b83`.
There is no local derived FreeIPA image or custom entrypoint to build.
[The hosted build and focused checks passed](https://github.com/Enucatl/freeipa-container/actions/runs/39307303697).
The versioned full-install harness also passed on Atlas with disposable
state and no published ports. The new server is initialized and healthy;
[initialization evidence](atlas-ipa-initialization-results.json) records its
certificate identities, authenticated checks and protected off-host backup.

## Initial preparation and startup

The one-time preparation command documented in Compose creates the separate
`pki_freeipa` internal intermediate
under the established Vault-held root. It never exports or resets that root. The
issuance role permits only `ipa.home.arpa`, using ML-DSA-44, and creates the shared
HTTP/LDAP certificate. Normal startup does not issue replacement identities.

Preparation writes `fullchain.pem`, `key.pem`, the public CA certificate
`ca.pem`, private `installer.json` passwords and `ipa-server-install-options`
under `docker/freeipa/secrets/`, beside Compose and excluded from Git. Compose
mounts the certificate inputs and protected options file alongside the persistent
`infra_ipa_data` volume. Existing installations and restored volumes use native
FreeIPA startup, without custom entrypoints, readiness checkpoints or volume
identity markers. Installation flags and mount paths are documented in Compose.

After the updated Atlas catalog is deployed and applied, Puppet's existing
`vault_cert` resource renews the PEM files. Its refresh command
imports the renewed certificate into both HTTP and Directory Server and restarts
the affected services. The Directory Manager password comes from
`secrets/installer.json`; no certificate JSON or custom renewal timer is needed.
See [the certificate lifecycle](openvox-infra.md#automated-vault-and-ipa-transport-renewal).

By default initialization generates and retains independent admin and Directory
Manager passwords. To supply declared credentials instead, pass
`--password-file /root/PROTECTED_INPUT.json`, containing `admin_password` and
`dm_password`. The input must belong to root and exclude group/other access.
Never put that file in Git.

Use Compose for ordinary startup and restoration, following the instructions in
the Compose file. The preparation helper is only for a fresh installation.

## DNS and client selection

The router publishes `ipa.home.arpa` at Atlas's reserved IPv4 address and stable
IPv6 ULA. Atlas remains the canonical PTR. The DNS-only deployment and 53 smoke
checks passed; see [the measured DNS evidence](atlas-ipa-dns-results.json).

New clients must explicitly select `ipa.home.arpa` and the authenticated new CA.
Existing clients retain `freeipa.home.arpa` until their migration. Do not globally
replace the common FreeIPA defaults before their cutover.

## Established identities and recovery

The completed account migration preserved the original users, numeric IDs,
memberships and policies. See [migration evidence](atlas-ipa-migration-results.json)
for the measured acceptance. Migration-only import and disposable clone tools
are retired. Native startup reads the existing `/data` volume; it does not
repeat that migration.

Recover the established volume and Compose-adjacent `secrets/` with the normal
[Atlas backup workflow](atlas-recovery.md). Check trusted HTTPS/LDAPS,
administrator Kerberos/API access and required user access after restoration.
FreeIPA remains CA-less, with PKINIT explicitly disabled and no Dogtag instance.

## Focused checks

```bash
uv run --frozen pytest -q tests/test_atlas_freeipa.py tests/test_native_compose_sources.py
```

Long builds and live installations in the main Codex thread use `wake-run`.
