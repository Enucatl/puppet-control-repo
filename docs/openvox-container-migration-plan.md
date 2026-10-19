# OpenVox container and fresh-chain deployment (#12)

Use the accepted image digest
`sha256:b27583dfa1f3adc3f96bdcee192b783e0044cfb02837456d459b8ca6eca59b97`
from `ghcr.io/enucatl/openvox-server`, build 24001. The new infra profile is
prepared, but production remains on the original host service. #13 owns its
cutover and production agent re-enrollment.

## Authority and activation order

1. Establish the ML-DSA-44 root with its private key generated and retained
   inside Vault, using authenticated bootstrap and protected recovery material.
   No custom CRL automation is required.
2. Prepare OpenVox's ML-DSA intermediate key/CSR on its owning host; have the
   Vault-held root sign it with path length zero. Separately generate the online
   Vault `pki_int` intermediate CSR and have the same root sign it.
   The root permits one intermediate level. Do not import or cross-sign old RSA.
3. Import the pinned public chains and signed root CRL through the explicit
   [infra PKI helper](openvox-infra.md). Ordinary startup must find existing
   authority state; it cannot initialize replacement identities. Restore uses
   exact archived identities, images and configuration, never the fresh path.
4. Configure Vault's own ML-DSA transport identity, TLS 1.3 bounds, scoped service
   roles and exact-name cert auth. Keep existing CRL behavior without added
   outbound enforcement, lifetime policy or distribution jobs. See
   [Vault limits](openvox-vault-limitations.md).
5. Deploy the real control repo through the container Git/r10k hook and enable
   the opt-in infra service only after trust, data, code, keys and CRLs pass
   preflight. Preserve direct agent TCP 8140 and direct Vault access.
6. Rehearse fresh enrollment, catalogs, renewal/revocation/replacement, restart,
   update/rollback and independent recovery before #13's production cutover.

The [disposable runbook](openvox-rehearsal.md) and
[implementation report](openvox-rehearsal-report.md) record actual verification.
The [historical offline-root runbook](openvox-offline-root.md) records same-key
renewal, serial revocation and public CRL export. Its physically offline signing
procedure is historical; the root key remains in Vault in the intended deployment. The new `--offline-root` entry points
in legacy scripts 01–04 prevent accidentally entering the old online-root,
default-RSA and host-systemd flow. Legacy default behavior remains available
for the existing production installation until its explicit retirement.

## Native clients

Supported accepted native targets are Ubuntu 24.04, Ubuntu 26.04 and Debian 13,
all amd64. The private #10 inventory records Ubuntu 24.04 x86_64 on Docker,
Forbearance and Complex, and Debian 13 x86_64 on both Proxmox hosts. This maps
the inventoried platforms to accepted package targets; it does not claim those
hosts have been upgraded or their full role catalogs applied.

On a genuinely fresh node, install the accepted package while keeping the
service masked, preseed operator-authenticated public trust, and enroll with a
single-use certname-bound token:

```sh
sudo bash proxmox/install-openvox.sh
sudo install -D -m 0644 /operator/authenticated-openvox-chain.pem \
  /etc/puppetlabs/puppet/ssl/certs/ca.pem
# Supply the token privately, without shell history or shell tracing.
sudo bash proxmox/configure-puppet.sh NODE_TYPE "$ENROLLMENT_TOKEN" NODE.home.arpa docker.home.arpa
```

For guest provisioning, set `OPENVOX_CA_CERT` to the authenticated public bundle
on the provisioning host. Docker/desktop and FreeIPA acceptance cloud-init
embed that bundle and the shared installer. The base template installs OpenVox
but leaves it disabled and unenrolled; guest cloud-init supplies its own trust
and identity. No Puppet Core credential is needed.

For existing Proxmox hosts, use `proxmox/enroll-proxmox.sh` only within the
planned #13 identity migration. It refuses different existing CA trust; the
shared enrollment helper refuses an existing non-ML-DSA key. Existing identities
are not converted by changing `key_type`. Back up and explicitly migrate them
through the cutover procedure.

The installer verifies fingerprint
`775661C709ACE6F7C1CF0FF6C4B7528F03E0BB94`, scopes `Signed-By`, installs the
matching OS preferences and selects an exact 24001 package version. Unsupported
OS/architecture combinations fail before package/service changes. Repeat
platform acceptance and replace preferences when changing OS; retain the
matching rollback artifacts independently.

## Completion boundaries

The isolated chain, transport, catalog, lifecycle, archive restore, rollback,
offline-root lifecycle and internal Vault key encoding have measured evidence.
Prepared infra and native caller changes require their final integration checks.
Puppetfile pins the published ML-DSA Vault Hiera transport and its small protocol-setting fix.

Remaining operational inputs include independent root custody/backups, actual
service endpoints, disposable native guest/full-role
catalog runs, a host reboot and cold bootstrap without the old services, and
hosted CI. #14/#8 own actual CA-less FreeIPA consumer compatibility; #18 owns
the later independent Atlas execution. No production rollout is implied by
the local rehearsal.
