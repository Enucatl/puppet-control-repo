# Atlas FreeIPA client certificate parsing

Ubuntu 26.04's `python3-ipalib` `4.13.1-1` asks Python cryptography to decode a
certificate public key before returning its SubjectPublicKeyInfo bytes. ML-DSA
keys raise `Unknown key type: 2.16.840.1.101.3.4.3.17` on that build, interrupting
client installation after the host has joined the realm.

The patch reuses the exact DER field extraction already used by the
[FreeIPA container fork](https://github.com/Enucatl/freeipa-container/tree/mldsa44/patches/mldsa.patch).
It preserves the original DER bytes and changes no certificate validation.

Build the library package:

```bash
bash proxmox/build-atlas-ipalib.sh /tmp/atlas-ipalib-packages
```

This repackages the official binary `python3-ipalib_4.13.1-1_all.deb`, downloaded
through Ubuntu's signed repository metadata. Only `ipalib/x509.py`, package
version `4.13.1-1+atlas1`, its checksum and the dependency metadata change.
The added `python3-packaging` dependency fixes a missing dependency exposed by
importing the library on a fresh minimal Ubuntu installation. The original
package is retained with the patched artifact and SHA-256 manifest for review.

Transfer that directory through authenticated SSH to
`/root/openvox-infra/ipalib`. The Atlas enrollment helper then invokes:

```bash
bash proxmox/install-atlas-ipalib.sh /root/openvox-infra/ipalib
```

The installer verifies package identity, version, architecture and checksums.
If the library is absent, apt installs the patched library and its required
dependencies without upgrading unrelated installed packages. It does not install
`freeipa-client`: Puppet owns client installation and enrollment. Existing
`4.13.1-1` installations receive just the patched library. Repeated calls verify
the installed files and return without changes. Other versions fail for review.

`check.py` compares ML-DSA-44, RSA and ECDSA SPKI bytes against OpenSSL's independent
DER output after installing the artifact in an isolated Ubuntu container.

The Puppet FreeIPA provider accepts completed matching enrollments unchanged.
An explicit incomplete installation marker requires a matching domain, server
and configured hostname before Puppet retries the native installer with
`--force-join`. FreeIPA's own installation check permits this incomplete state;
its uninstaller refuses it as not configured. Existing sysrestore backups stay
available. Completed or unassigned hosts do not receive forced enrollment.
A successful installer exit must also leave its completion marker set to true.

No apt holds are installed. Review or retire this backport when the distro
library gains support or publishes a new revision; do not replace a newer distro
package with this older revision.
