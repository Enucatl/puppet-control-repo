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

The remaining acceptance work concerns ML-DSA installation and authentication,
TLS negotiation, Hiera, normal convergence, authority-preserving recovery and
rebooting a disposable Docker host.

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

Final release pins and the disposable Docker host reboot result are recorded in
the main rehearsal report after the baseline rollback is complete. Earlier
strict-CRL acceptance is historical evidence and is not a remaining requirement.

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

## Protected historical artifacts

Lab recovery inputs and archived harnesses are retained under
`root@proxmox.home.arpa:/root/openvox-issue12-rehearsal`, with directory mode 0700
and file mode 0600. Local copies are under
`/tmp/openvox-closure-ops/private`. They contain sensitive **test** recovery
inputs and must not be published.

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
Vault recovery-file metadata with passwordless sudo; #11/#18 must establish that
access before production cutover. Off-host filesystem and SSH independence do not
by themselves establish independent physical disks.

No human emails were sent. The retired CRL failure-mail test used a local capture
sink and is not evidence of operational mailbox delivery. Full role catalog
compilation is separate from applying infrastructure profiles against production
dependencies.
