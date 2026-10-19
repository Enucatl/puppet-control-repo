# Atlas CA-less FreeIPA

This is the scripted implementation for [rehearsal #14](https://github.com/Enucatl/puppet-control-repo/issues/14)
and [deployment #8](https://github.com/Enucatl/puppet-control-repo/issues/8).
Atlas's native OpenVox enrollment waits for the new identity service and its
required accounts. The new endpoint is `ipa.home.arpa`; the existing
`freeipa.home.arpa` service remains on Docker during preparation.

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

`docker/freeipa/atlas.Dockerfile` adds only the Atlas entrypoint to that image.
Its published base is pinned to
`sha256:80024f5e76ce4eedd986eb9e7ed63461cafb1bc1774386cf4ff41a259633d0cb`,
from fork revision `cea6c6b250867cd9dbf23c7224d81d1ae7908b83`.
[The hosted build and focused checks passed](https://github.com/Enucatl/freeipa-container/actions/runs/39307303697).
The versioned full-install harness also passed on Atlas with disposable
state and no published ports. Production deployment waits for image review.

`atlas-entrypoint.py` reads protected inputs, validates the prepared data volume
identity, writes the image's supported installer options file and delegates to
the upstream init. It refuses missing established state rather than silently
creating another realm. Credentials are not supplied through Compose environment
variables or stored in the repository.

Build on Atlas from its versioned control checkout:

```bash
sudo docker compose --env-file /dev/null -p infra \
  -f docker/freeipa/atlas-compose.yaml build ipa
```

## Initial preparation and startup

From the clean published control checkout on the promoted Atlas host:

```bash
sudo python3 proxmox/atlas-freeipa.py initialize
```

This explicit operation creates the separate `pki_freeipa` internal intermediate
under the established Vault-held root. It never exports or resets that root. The
issuance role permits only `ipa.home.arpa`, using ML-DSA-44, and creates separate
HTTP/LDAP leaves. Normal startup does not issue replacement identities.

Canonical private checkpoints, credentials and PEM files are under
`/root/openvox-infra/freeipa`, protected by root ownership and restrictive modes.
Container inputs are copied into its `container-inputs` subdirectory, owned by
the existing Docker remapped root (`100000:100000`) with directory mode `0700`
and file mode `0600`. Compose mounts only that subdirectory, read-only. The
external `infra_ipa_data` volume holds the server state; its authority label and
UUID marker identify the original data, including after an explicit restore.

By default initialization generates and retains independent admin and Directory
Manager passwords. To supply declared credentials instead, pass
`--password-file /root/PROTECTED_INPUT.json`, containing `admin_password` and
`dm_password`. The input must belong to root and exclude group/other access.
Never put that file in Git.

After preparation, ordinary startup is:

```bash
sudo python3 proxmox/atlas-freeipa.py start
```

Startup checks the saved issuer/key, all prepared files and the data volume.
It starts only the IPA service, verifies its CA-less healthcheck and records
successful installation. Missing established identities require recovery from
protected state. Do not rerun initialization as a recovery procedure.

## DNS and client selection

The router publishes `ipa.home.arpa` at Atlas's reserved IPv4 address and stable
IPv6 ULA. Atlas remains the canonical PTR. The DNS-only deployment and 53 smoke
checks passed; see [the measured DNS evidence](atlas-ipa-dns-results.json).

New clients must explicitly select `ipa.home.arpa` and the authenticated new CA.
Existing clients retain `freeipa.home.arpa` until their migration. Do not globally
replace the common FreeIPA defaults before their cutover.

## Identity migration and acceptance

A fresh CA-less installation is not account migration. Preserve original users,
password hashes, numeric IDs, principals and policies through the tested #14
procedure before declaring this endpoint ready for Atlas's full catalog.
`ipa-migrate prod-mode` is the upstream migration path to rehearse; stage mode
regenerates IDs. Fresh-realm migration does not preserve the original Kerberos
master key or existing client keytabs, so retain original service state and
script the agreed client re-enrollment and password-migration behavior.

Do not treat the image's successful certificate import or a healthy fresh server
as proof of account preservation, renewal, client enrollment or recovery. #14
also requires real HTTP/LDAP/Kerberos checks, unchanged required access, renewal
and reload, absence of Dogtag and the original-deployment rollback procedure.

DNS is deployed and the image's fresh-install checks passed. Record real
initialization and account-migration acceptance separately before Atlas's first
full catalog; an initialized empty server does not satisfy that prerequisite.

## Focused checks

```bash
uv run --frozen pytest -q tests/test_atlas_freeipa.py \
  tests/test_atlas_freeipa_entrypoint.py tests/test_router_dns.py
```

Long builds and live installations in the main Codex thread use `wake-run`.
