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

## Completed acceptance (19 October 2026)

Clean signed release [resolute-24001](https://github.com/Enucatl/openvox-packages/releases/tag/resolute-24001)
passed full source tests and installed-package acceptance on Ubuntu 26.04,
Ubuntu 24.04 and Debian 13, followed by image publication in
[39219562955](https://github.com/Enucatl/openvox-packages/actions/runs/39219562955).
The image is pinned at
`sha256:b27583dfa1f3adc3f96bdcee192b783e0044cfb02837456d459b8ca6eca59b97`.
Its sources are SSL-utils `bff2e9b99a1bc4280d5b5f346de29a6b33296a88`,
server `54400ff7818c4ef5e3e24b281e5d5801488bbfb6` and Hiera
`cbdbb59a424b69570f0c957689ddbf537a6866a0`.

Hosted CI with the final 24001 pins passed at `3ac07f4` in
[39225478175](https://github.com/Enucatl/puppet-control-repo/actions/runs/39225478175).
Local verification passed 322 regression tests, eight actual isolated PKI import
checks and 40 artifact-pin checks. Both native guests upgraded to signed 24001
packages without changing their ML-DSA keys, then passed real Hiera-backed
catalog convergence and full native TLS handshakes against the final image.
Their effective `certificate_revocation=leaf` setting matches the legacy shared
enrollment helper; the packaged upstream default was also tested during recovery.

Sanitized [acceptance evidence](openvox-acceptance-results.json) records exact
packages, source/image pins, public authority fingerprints, OS image checksums,
archive hashes, boot IDs, measured TLS and catalog results. It contains no
private keys, credentials, recovery inputs or secret values.

Disposable Ubuntu 24.04 and Debian 13 guests passed signed installation, actual
Python autosigner enrollment, real Vault-backed catalogs, convergence and reboot.
Their signed upgrades preserved their ML-DSA identity keys. Complete Docker,
desktop and Proxmox catalogs compile from real guest facts with dummy secrets.
Compilation is not full role application.

The actual hardened Compose stack passed startup and automatic Vault unseal
without source/JAR overlays. A clean 24001 kit contains only the three baseline
services, exact images, public bind configuration, consistent stopped volumes
and separately protected Vault recovery/unseal inputs. It was streamed to
`root@proxmox.home.arpa:/root/openvox-issue12-rehearsal/final-baseline-24001`
(directory 0700, files 0600), hash-verified and restored using its independently
archived standalone harness into `openvox-baseline-restore`. The original
containers were stopped, their boot unit disabled and their bind root absent.
The restore needed no original Vault, GitHub, APT or registry access. No retired
CRL cleanup, publisher setting or compatibility environment was required.

On the restored final 24001 stack:

- All three services became healthy and Vault unsealed automatically.
- Root, issuer, server and native identities matched the established originals.
- Both Ubuntu and Debian clients completed fresh Hiera-backed catalogs with
  exit 0 and measured TLS 1.3, X25519MLKEM768 and ML-DSA in both directions.
- Exact image-only rollback `24001 -> 18001 -> 24001` preserved identities and
  allowed a clean catalog at both steps.
- An actual Docker-host OS reboot changed the kernel boot ID and restored all
  services plus the native agent. Its unattended fresh catalog was unchanged
  with `cached_catalog_status=not_used`; both native clients then reconverged.

## Closure and follow-up ownership

The isolated integration and recovery acceptance for #12 is complete. No manual
intervention was required for the completed lab steps. Reproduction inputs and
commands are in the [operational record](openvox-operational-acceptance.md).

Shared provisioning and the Atlas base belong to #15, whose #12 prerequisite is
now satisfied. Live Atlas convergence belongs to #13 and full desktop/GPU role
acceptance to #16/#17; FreeIPA consumers belong
to #14/#8. Requiring those complete migrations to close #12 would create a
dependency cycle. The native enrollment and fixture catalog applications prove
the OpenVox integration; complete role compilation is recorded separately and
is not represented as full workload application. See the operational record
for the concrete dependency boundary. Production endpoints are not rehearsal
fixtures.

#14/#8 own actual CA-less FreeIPA consumer compatibility, #11 production backup
custody, #18 full Atlas execution and #13 production cutover. BCJSSE conditional
`disabledAlgorithms` expressions remain unsupported; controlled deployment policy
and the accepted Vault exception are documented in
[the runtime audit](openvox-jvm-policy.md) and [Vault limits](openvox-vault-limitations.md).
Automatic Vault TLS certificate renewal is not implemented here. Production
backup custody, physical failure-domain independence and full workload recovery
are not established by fresh lab recovery. No production rollout is implied.
