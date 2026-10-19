# OpenVox issue #12 implementation and acceptance

## Current scope

Work is isolated in `/home/user/code/puppet-control-repo`, on GitHub-only `dev`.
The original `/export/docker/puppet-control-repo`, production deployment branch,
existing services, identities, routing and DNS are unchanged. Production rollout
belongs to #13.

On 19 October the user removed strict CRL enforcement and automation from scope.
The implementation now retains the pre-existing CRL behavior. Removed additions:

- Custom Hiera and Python outbound CRL verification and required CRL bundles.
- `crl-ttl`, issuer-bound CRL lifetimes and startup normalization in the forks.
- Publisher/client containers, systemd jobs, alerts, AppRole credentials,
  authorization exceptions, rollback barriers and Vault worker configuration.
- Native full-chain overrides and forced refresh intervals.

The packaged CA still maintains its ordinary CRLs, and native enrollment retains
`certificate_revocation=leaf`. Certificate-chain, hostname and expiry checks,
ML-DSA identities, signed package installation, TLS configuration and exact-name
single-use enrollment authorization remain enabled. No CRL freshness/distribution
work is a prerequisite for closing #12.

## Delivered implementation

- Opt-in OpenVox service in the actual hardened infra Compose model, persistent
  authority/SSL/code/Git/r10k/runtime state, nonroot foreground startup, readiness
  and fail-on-missing-state startup. JRuby's temporary native extraction mount
  explicitly allows execution; the image remains read-only.
- Fresh Vault-held ML-DSA-44 root and separate OpenVox/Vault intermediates.
  Root private material stays in Vault; importing/restoring established state
  cannot silently create a replacement identity.
- Verified TLS 1.3 configuration for OpenVox and Vault. OpenVox uses hybrid
  X25519MLKEM768 and ML-DSA authentication. Vault's classical-only TLS 1.3
  fallback remains an explicitly accepted temporary exception.
- Hiera uses authenticated explicit CA trust and ML-DSA-capable Java TLS.
  A small independent fix propagates the configured outbound protocol list.
- Signed native installation and authenticated trust bootstrap on supported
  Ubuntu/Debian targets; protected credential encoding and identity preflight.
- Existing Vault unsealer authenticates a locally provisioned public-only TLS
  key pin for health/unseal. Wrong/missing pins send no shares; a matching expired
  certificate can recover a sealed Vault. General clients retain normal TLS
  certificate verification.

## Evidence and current verification

Signed release 22001 and its image passed full source tests and installed-package
acceptance on Ubuntu 26.04, Ubuntu 24.04 and Debian 13 in
[39212625422](https://github.com/Enucatl/openvox-packages/actions/runs/39212625422).
Hosted control-repo CI passed at `faa9e14` in
[39217322360](https://github.com/Enucatl/puppet-control-repo/actions/runs/39217322360).
These runs precede removal of the CRL additions; a replacement release and
post-removal CI are pending. Published artifacts are immutable; rewritten fork
branches do not alter an existing package or image.

Disposable Ubuntu 24.04 and Debian 13 guests passed signed installation, actual
Python autosigner enrollment, real Vault-backed catalogs, convergence and reboot.
Their signed upgrades preserved their ML-DSA identity keys. Complete Docker,
desktop and Proxmox catalogs compile from real guest facts with dummy secrets.
Compilation is not full role application.

The actual hardened Compose stack passed startup, Vault restart and automatic
unseal without source/JAR overlays. Fresh lab state was archived to a protected
root-only directory on a separate Proxmox host, downloaded with verified hashes,
and restored on a disposable Ubuntu Docker host while the source stack and bind
staging were unavailable. This establishes recovery of fresh test authority
state, not production backup custody or full Atlas recovery.

The earlier strict-CRL experiment is superseded. Its image-only rollback preserved
identities but failed our added 72-hour policy on 18001. That policy and its
rollback gate have been removed. Historical commits retain the experiment;
its timers, publisher and credentials are not part of the deployment.

## Remaining closure work

1. Publish and pin the clean fork release, then run post-removal source/hosted CI
   and native service acceptance using those exact artifacts.
2. Complete baseline disposable Docker-host reboot and image rollback/recovery
   checks with no CRL jobs or custom lifetime settings installed.
3. Record final sanitized identity, TLS, code, authorization and recovery evidence
   in the [operational acceptance record](openvox-operational-acceptance.md).
4. Resolve the full-role/provisioning coverage gap. Safe full role application
   needs isolated FreeIPA/NFS and related dependencies; production endpoints
   must not be used merely to complete a rehearsal.

#14/#8 own actual CA-less FreeIPA consumer compatibility, #11 production backup
custody, #18 full Atlas execution and #13 production cutover. BCJSSE conditional
`disabledAlgorithms` expressions remain unsupported; controlled deployment policy
and the accepted Vault exception are documented in
[the runtime audit](openvox-jvm-policy.md) and [Vault limits](openvox-vault-limitations.md).
Automatic Vault TLS certificate renewal and external alert delivery remain
unverified. Issue #12 stays open until its remaining acceptance is satisfied.
