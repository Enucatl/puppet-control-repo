# Atlas curl channel binding backport

Ubuntu 26.04's `curl` / `libcurl4t64` `8.18.0-1ubuntu2.8` cannot calculate
Negotiate channel bindings for an ML-DSA certificate. `ipa-join` reports the
generic curl error 91; verbose curl identifies `digest algorithm UNDEF (NID 0)`.

The vendored patch is the unmodified upstream curl commit
[`b0f6e9a3d7be02e8f2e323aa97f2ff9b30662b32`](https://github.com/curl/curl/commit/b0f6e9a3d7be02e8f2e323aa97f2ff9b30662b32).
For digestless signatures it omits the undefined `tls-server-end-point` binding
as permitted by [RFC 5929 section 4.1](https://www.rfc-editor.org/rfc/rfc5929#section-4.1).
Classical certificate channel binding remains enabled. TLS certificate and
hostname verification and Kerberos server authentication remain enabled.

Build the two packages from Ubuntu's signed source archive:

```bash
bash proxmox/build-atlas-curl.sh /tmp/atlas-curl-packages
```

The Dockerfile pins its base image, source version and upstream patch. Ubuntu
archive signatures authenticate source checksums. It builds the distro packages
with their existing configuration and skips the upstream test suite; the focused
check below exercises the installed artifacts. Both packages receive version
`8.18.0-1ubuntu2.8+atlas1` because curl requires the exact matching libcurl version.

Run the focused check in the isolated build image:

```bash
docker build --target build -t atlas-curl-validation docker/curl
docker run --rm -i atlas-curl-validation sh -c \
  'dpkg -i /artifacts/libcurl4t64_*_amd64.deb /artifacts/curl_*_amd64.deb >/dev/null && python3 -' \
  < docker/curl/check.py
```

This checks ML-DSA-44, RSA and ECDSA Negotiate challenges plus wrong CA and
hostname rejection. It uses temporary test certificates without real credentials.

Transfer the complete artifact directory through authenticated SSH to
`/root/openvox-infra/curl` on Atlas, then invoke the reviewed installer:

```bash
bash proxmox/install-atlas-curl.sh /root/openvox-infra/curl
```

The installer checks SHA-256 sums, package identities, versions, architecture,
Ubuntu release and the existing package versions before replacing exactly these
two packages. A repeat checks the installed package files and returns unchanged.
The Atlas enrollment helper invokes this dependency installer before Puppet runs;
Puppet still owns FreeIPA client installation and enrollment.

No apt hold or preference is installed. Newer distro security updates can replace
this backport. Before bootstrapping against a newer curl revision, review whether
the upstream fix is present, then retire this dependency installer or refresh the
backport's source version and artifacts. Unexpected versions fail bootstrap so an
older backport cannot silently replace a newer security update.
