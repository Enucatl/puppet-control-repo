# Native OpenVox clients

Atlas serves production catalogs through `openvox.home.arpa`. Use the
[authority runbook](openvox-infra.md) for server startup and
[Atlas recovery](atlas-recovery.md) for established state. Completed deployment
plans and acceptance results are in the [migration archive](archive/migration/README.md).

## Fresh enrollment

Supported accepted native targets are Ubuntu 24.04, Ubuntu 26.04 and Debian 13,
all amd64.

On a genuinely fresh node, install the accepted package while keeping the
service masked, preseed operator-authenticated public trust, and enroll with a
single-use certname-bound token:

```sh
sudo bash proxmox/install-openvox.sh
sudo install -D -m 0644 /operator/authenticated-openvox-chain.pem \
  /etc/puppetlabs/puppet/ssl/certs/ca.pem
# Supply the token privately, without shell history or shell tracing.
sudo bash proxmox/configure-puppet.sh NODE_TYPE "$ENROLLMENT_TOKEN" NODE.home.arpa openvox.home.arpa
```

For guest provisioning, set `OPENVOX_CA_CERT` to the authenticated public bundle
on the provisioning host. Docker/desktop and FreeIPA acceptance cloud-init
embed that bundle and the shared installer. The base template installs OpenVox
but leaves it disabled and unenrolled; guest cloud-init supplies its own trust
and identity. No Puppet Core credential is needed.

Atlas uses [atlas-enroll.py](../proxmox/atlas-enroll.py) after its OpenVox/Vault
services are ready. Native Proxmox enrollment uses
[enroll-proxmox.sh](../proxmox/enroll-proxmox.sh). That helper refuses different
existing CA trust; the shared enrollment helper refuses an existing non-ML-DSA
key. Changing `key_type` does not convert existing identities. Back them up and
explicitly migrate them before enrolling against a different authority.

The installer verifies fingerprint
`775661C709ACE6F7C1CF0FF6C4B7528F03E0BB94`, scopes `Signed-By`, installs the
matching OS preferences and selects an exact 28001 package version. Unsupported
OS/architecture combinations fail before package/service changes. Repeat
platform acceptance and replace preferences when changing OS; retain the
matching rollback artifacts independently.

## Verification

The [disposable rehearsal](openvox-rehearsal.md) exercises enrollment, catalogs,
renewal and TLS rejection. Historical measured results are retained in the
[migration archive](archive/migration/README.md). Validate each host's role and
workloads after provisioning; package acceptance alone does not verify them.
