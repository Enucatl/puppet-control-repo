# Issue 12 operational acceptance

The acceptance environment uses fresh test authorities, disposable native guests
and isolated Docker projects. Existing production configuration and service state
were unchanged. A new protected directory on Proxmox holds only lab recovery
artifacts.

## Current scope

At the user's request, custom CRL lifetime policy, strict CRL validation and CRL
refresh automation are removed. Baseline Vault/OpenVox revocation behaviour
remains. No CRL publisher service, independent client timer, publisher AppRole or
failure-mail machinery is required for #12.

The completed #12 acceptance covers ML-DSA installation and authentication,
TLS negotiation, Hiera, normal convergence, authority-preserving recovery and
rebooting a disposable Docker host. Production Atlas execution remains #18,
new-chain deployment/enrollment #13 and service cutover #19.

## Completed baseline evidence

- Native Ubuntu and Debian installation through the signed package installer.
- Enrollment with exact-name, single-use tokens, real Vault-backed Hiera lookup,
  subsequent convergence and native guest reboots.
- Observed TLS 1.3, X25519MLKEM768 and ML-DSA-44 during real agent requests.
- Actual hardened infrastructure Compose startup with a read-only root filesystem.
- Protected off-host archive transfer and checksum verification, followed by
  restoration into a separate Docker host VM with the original authority
  containers stopped and their bind-mounted source directory unavailable.
- Automatic Vault unsealing from the restored established identity.

The final 24001 image and signed native packages passed service acceptance,
protected archive-only fresh-namespace restore, exact `24001 -> 18001 -> 24001`
rollback and Docker-host OS reboot. All three services recovered, Vault unsealed,
and the agent obtained an unattended fresh unchanged catalog without a cached
catalog. Authority and agent identities were preserved. Both native clients
then passed TLS and catalog convergence. See the
[sanitized results](openvox-acceptance-results.json) and
[final report](openvox-rehearsal-report.md).

## Startup fixes retained

OpenVox needs an explicitly executable `/tmp` for JRuby native FFI extraction.
Docker otherwise mounts the tmpfs with `noexec`, causing Puppet POSIX detection
to fail. Its root filesystem remains read-only.

The opt-in Vault override mounts only `certificates/public` into the existing
unsealer. The authenticated certificate writer publishes `vault.pub.pem` there
atomically, without exposing Vault's TLS private key to the unsealer. The
`sys/health` and `sys/unseal` bootstrap requests use TLS 1.3 authenticated by that
locally provisioned public-key pin. They can therefore recover a sealed Vault
even after its TLS certificate expires. Wrong or missing pins transmit no unseal
share. This bootstrap mechanism does not introduce a CRL service or scheduler.

Every authenticated TLS key replacement must publish its matching public pin
before Vault reloads. The existing 6.5-year Vault TLS issuance has no automatic
renewal job in this repository; this work does not claim to add one.

## Protected recovery artifacts

The final clean kit is retained at
`root@proxmox.home.arpa:/root/openvox-issue12-rehearsal/final-baseline-24001`,
with directory mode 0700 and file mode 0600. It contains sensitive **test**
recovery inputs and must not be published. Its hashes and image provenance are
included in the public sanitized results. The large image archive is retained
only off-host and was streamed into Docker without duplicate local image files.

Revisions 1–3 preserve the earlier strict-CRL rehearsal. They are historical
snapshots, not the baseline deployment kit: restoring them also restores the
retired CRL policy and requires the recorded baseline cleanup. Do not advertise
those snapshots as the final baseline configuration.

The archived standalone restore requires a fresh Docker host with Docker and
Compose already installed, authenticated root SSH access to the protected backup,
loaded exact images, and its verified configuration/volume/recovery archives. It
needs no original Vault, GitHub, APT or registry during the restore itself.
Image provenance records filesystem layers because image IDs can differ between
Docker image stores; digest-only images receive explicit offline recovery tags.
Numeric container ownership 999/100 is preserved across restoration from the
source daemon's user namespaces into ordinary guest Docker.

## Remaining operational limits

A fresh-authority restore does not prove production backup custody or restore
FreeIPA/NFS application state. The current SSH account cannot inspect production
Vault recovery-file metadata with passwordless sudo during the rehearsal; #18
must verify independently available production recovery access before cutover.
#11 remains closed pre-rollout evidence. Off-host filesystem and SSH independence do not
by themselves establish independent physical disks.

No human emails were sent; the custom CRL alert machinery was removed. Full
role catalog compilation is separate from applying infrastructure profiles
against production dependencies.

## Provisioning and full-role boundary

The native guests ran the real signed installer and
[`configure-puppet.sh`](../proxmox/configure-puppet.sh), enrolled against the
isolated Compose server, applied a marker catalog with a real Vault-backed Hiera
lookup, and converged again. Complete Docker, desktop and Proxmox catalogs were
also compiled with actual native Facter facts. **Compilation and the applied
marker catalog are not full-role application.**

Real roles come from [`manifests/site.pp`](../manifests/site.pp),
[`data/common.yaml`](../data/common.yaml) and [`data/roles`](../data/roles).
Common classes require `freeipa::client`, `freeipa_users`,
[`profile::common::identity`](../modules/profile/manifests/common/identity.pp)
and [`profile::common::certs`](../modules/profile/manifests/common/certs.pp): a
complete apply therefore needs enrolled FreeIPA/SSSD identities and scoped Vault
issuance. Docker/desktop roles add NFS and toolchains. The actual
[`docker.home.arpa` node data](../data/nodes/docker.home.arpa.yaml) also activates
Kerberos mounts, exports and Git deployment projects; it is not a generic lab
fixture.

[#15](https://github.com/Enucatl/puppet-control-repo/issues/15) owns shared
provisioning, the repeatable Atlas base and designated-ID target handling.
#13 owns live Atlas enrollment/convergence; #16/#17 own full desktop/GPU workload
acceptance. #12 is closed isolated evidence, not blocked on these later tasks.
FreeIPA consumer compatibility belongs to #14/#8; #18 verifies production
identity-state recovery using #11's pre-rollout recovery evidence.

The actual cloud-init sources are
[`docker-cloud-init.yml.tmpl`](../proxmox/docker-cloud-init.yml.tmpl),
[`desktop_cloud_init.py`](../proxmox/desktop_cloud_init.py) and
[`ubuntu-cloud-init.yml.tmpl`](../proxmox/ubuntu-cloud-init.yml.tmpl). Rendering and
enrollment have regression coverage; shared base provisioning remains #15 and
full-role deployment #13/#16/#17. A future isolated test can boot rendered output in a fresh
local VM; full-role application additionally needs real isolated
FreeIPA/Kerberos and configured NFS/service dependencies.

[`docker-server.sh`](../proxmox/docker-server.sh) and
[`ubuntu-server-template.sh`](../proxmox/ubuntu-server-template.sh) validate
cluster inventory and the selected target before token issuance or guest mutation.
Rebuilds require an explicit flag and matching local guest name and ownership
marker. Template 9000 is protected, and failure cleanup rechecks ownership.
Atlas base creation passed twice; live enrollment and full-role acceptance
remain with #13/#16/#17.

## Final baseline recovery contract

Protected lab bundle: `root@proxmox.home.arpa:/root/openvox-issue12-rehearsal/final-baseline-24001`.
Directory mode 0700; files mode 0600. It contains fresh test identities and sensitive
recovery inputs; publish only `archive-summary.json` (checksums/provenance).

The kit contains the clean three-service resolved Compose configuration, public
bind inputs, consistent stopped named volumes, separate Vault recovery/unseal
inputs, exact images with offline-loadable tags, image filesystem provenance,
and the standalone restore harness. No retired CRL policy or cleanup is needed.
The final image derives from published digest
`sha256:b27583dfa1f3adc3f96bdcee192b783e0044cfb02837456d459b8ca6eca59b97`.
Hiera is `cbdbb59a424b69570f0c957689ddbf537a6866a0` in archived code/cache.

Prerequisites: a disposable Ubuntu Docker host with Docker/Compose already
installed, enough disk space for restored layers/volumes, authenticated SSH access
to the protected archive and target VM, and no existing baseline restore project.
The actual restore uses no original Vault, GitHub, APT or registry access.

The lab orchestration uses `/tmp/openvox-closure-baseline/transfer-baseline.py`:

```bash
# Native acceptance first stops/disables the original stack and its agent,
# then archives its established state inside the VM (never reinitializes PKI):
sudo python3 archive-baseline.py /opt/openvox-closure-recovery/infra.json /root/openvox-baseline-backup

# On the control host, stream state and exact images into the new protected
# off-host directory, then stream only those verified archives back into VM:
python3 /tmp/openvox-closure-baseline/transfer-baseline.py upload
python3 /tmp/openvox-closure-baseline/transfer-baseline.py download

# Inside VM, original source stack remains stopped/disabled and its bind root
# has been renamed. Execute the independently archived standalone harness:
sudo python3 /root/openvox-baseline-inputs/restore-baseline.py /root/openvox-baseline-inputs
```

`harness.tar.gz` contains the scripts; its SHA256 is recorded in the protected
manifest and sanitized summary. The orchestration's SSH destinations/identity-file
paths are specific to this disposable lab. The standalone restore needs only its
backup directory and ordinary local Docker access; it has no control-repo or
source-host dependency.

The restored project is `openvox-baseline-restore`, rooted at
`/opt/openvox-baseline-restore`, with enabled `openvox-baseline-restore.service`
after Docker. Its only services are OpenVox, Vault and the existing unsealer.
Endpoints are loopback 8140/8200; the restored private Docker aliases are the
established certificate hostnames. Numeric ownership 999/100 is preserved.
Missing identities are not regenerated. Image filesystem layers are verified
because engine image IDs can differ across Docker image stores.

No large image archive is stored on the VM or control host: Docker save/load is
streamed through authenticated SSH, and the compressed archive is retained only
on the protected off-host target. The original configuration and source volumes
are preserved separately. Subsequent evidence compares authority/native key
fingerprints and real Hiera/TLS/catalog convergence, including disposable host
reboot and image rollback. This proves fresh-lab recovery, not production backup
custody or physical-disk independence.
