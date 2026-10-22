# Completed migration records

Atlas, Aperture and Cognition replaced Docker, Forbearance and Complex;
Cerebrum subsequently replaced Cognition with XFS. These
records preserve plans, investigation notes and acceptance results from their
original revisions. Commands and staging status in them are historical; some
referenced helpers have been retired and remain available in Git history.
Retaining these records does not authorize deleting rollback VMs, snapshots or
protected backups. The cutover records retain their original retention limits.
Retired host catalogs, role assignments and scripts remain in Git history;
rollback requiring an old host must use its matching historical control revision.
Repository cleanup does not remove VM disks, snapshots or protected backups.

For current operations, use [VM provisioning and publication](../../../proxmox/README.md),
[OpenVox/Vault startup](../../openvox-infra.md), [native clients](../../openvox-native-clients.md),
[FreeIPA](../../atlas-freeipa.md), [service configuration](../../atlas-services.md),
and [Atlas recovery](../../atlas-recovery.md). The reusable
[OpenVox rehearsal](../../openvox-rehearsal.md) and
[FreeIPA acceptance fixture](../../../proxmox/freeipa-client-acceptance.sh) remain active.

## Plans and investigations

- [Ubuntu 26.04 migration](26.04-migration-plan.md)
- [Modern cryptography migration](modern-cryptography-migration-plan.md)
- [Earlier cryptography risk assessment](post-quantum.md)
- [Legacy Vault issuer-encoding investigation](05-vault-cert-auth-puppet-issuer-encoding.md)
- [Legacy FreeIPA client v7 rollout](freeipa-client-v7-rollout.md)

## OpenVox acceptance

- [Rehearsal report](openvox-rehearsal-report.md)
- [Operational acceptance](openvox-operational-acceptance.md)
- [Rehearsal results](openvox-rehearsal-results.json)
- [Final acceptance results](openvox-acceptance-results.json)

## Atlas acceptance

- [Base provisioning](atlas-provisioning-results.json)
- [Tooling bootstrap](atlas-bootstrap-results.json)
- [Host configuration](atlas-configuration-results.json)
- [Git access](atlas-git-access-results.json)
- [Git publication](atlas-git-publishing-results.json)
- [Infrastructure activation](atlas-infra-results.json)
- [Enrollment](atlas-enrollment-results.json)
- [OpenVox renewal](atlas-openvox-renewal-results.json)
- [Maintenance and recovery](atlas-maintenance-results.json)
- [DNS](atlas-dns-results.json)
- [FreeIPA initialization](atlas-ipa-initialization-results.json)
- [FreeIPA migration](atlas-ipa-migration-results.json)
- [FreeIPA DNS](atlas-ipa-dns-results.json)
- [FreeIPA image upgrade](atlas-ipa-image-upgrade-results.json)

## Desktop and GPU cutovers

- [Aperture cutover and recovery records](aperture-cutover.md)
- [Aperture acceptance results](aperture-cutover-results.json)

- [Cerebrum XFS migration and acceptance](cerebrum-migration.md)
- [Cerebrum handover and rollback](cerebrum-cutover.md)
