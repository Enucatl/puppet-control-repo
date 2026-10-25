# Atlas CA-less FreeIPA

This is the scripted implementation for [rehearsal #14](https://github.com/Enucatl/puppet-control-repo/issues/14)
and [deployment #8](https://github.com/Enucatl/puppet-control-repo/issues/8).
The final live import preserved all 10 users and 16 groups, including their
IDs, memberships, HBAC and sudo policies. Atlas is now enrolled through
OpenVox's `freeipa::client` resource against `ipa.home.arpa`; its full Docker
catalog passed. Both `ipa.home.arpa` and the compatibility name
`freeipa.home.arpa` now resolve to Atlas; the old Docker VM is stopped.
See [migration evidence](archive/migration/atlas-ipa-migration-results.json) and
[enrollment evidence](archive/migration/atlas-enrollment-results.json).

The realm remains `HOME.ARPA` and the domain remains `home.arpa`. The operator
confirmed that password Kerberos and SSH GSSAPI are sufficient: the installer
explicitly uses `--no-pkinit`. It supplies HTTP/LDAP leaves and CA trust directly,
without `--external-ca`, Dogtag installation, or a subordinate Dogtag CSR.

## Image

The image build lives in the
[FreeIPA container fork](https://github.com/Enucatl/freeipa-container).
It uses CentOS Stream 10 and the distribution's cryptography package for ML-DSA
keys. A small FreeIPA patch handles seed-only key extraction and public-key
matching. NSS retains full-chain and hostname validation.

The [Compose file](../docker/docker-compose.yml) selects the published
image directly, with its native entrypoint. It owns the image pin, install
options, volume mount, healthcheck and startup instructions. The pinned image
comes from fork revision `63b0aa68606946268dcaff1e2d1113febada1183`, pinned to
`sha256:bce0ae74c51bdad84335d36561ce5915ddb985ac9237d8d1761f3e3c2e387a66`.
There is no local derived FreeIPA image, custom entrypoint or entrypoint override.
[The hosted build and checks passed](https://github.com/Enucatl/freeipa-container/actions/runs/39654753564),
including fresh installation, HTTP/LDAP certificate replacement and a standard
installation with a replica. The established server's
[initialization evidence](archive/migration/atlas-ipa-initialization-results.json) records its
certificate identities, authenticated checks and protected off-host backup.

The [October 21 image acceptance](archive/migration/atlas-ipa-image-upgrade-results.json) passed
on Atlas using the published digest: fresh installation, ML-DSA-44/65/87 and
RSA/EC compatibility, certificate replacement, existing-volume migration and
ordinary native startup. The live service is healthy on that digest. All 10
users, 16 groups, numeric IDs, memberships, HBAC and sudo policies were preserved.
Both HTTPS and LDAP now serve the current Vault-issued ML-DSA-44 certificate.
Vault preparation/renewal and account provisioning helpers remain infrastructure
integration; they do not patch the image or supply ML-DSA compatibility code.

### One-time AlmaLinux to CentOS Stream transition

The old AlmaLinux data records FreeIPA `4.13.4-1.el10_2`, while this CentOS image
packages `4.13.4-1.el10`. Native startup treats the release suffix as a downgrade
and exits. After testing a stopped-volume copy, the live migration temporarily
set the image's native `DEBUG_NO_EXIT=1` environment option, waited for
`ipa-server-upgrade.service` to fail its version check, then ran inside the
container:

```sh
ipa-server-upgrade --skip-version-check
/usr/sbin/ipa-server-configure-first upgrade
systemctl reset-failed ipa-server-upgrade.service
```

Recreating with the ordinary Compose definition removed `DEBUG_NO_EXIT` and
passed health and authentication checks. No migration flag, wrapper, image
modification or startup script remains. This procedure applies to the tested
4.13.4 distribution-release transition; another version transition requires its
own clone test. Restoring an already migrated backup uses ordinary Compose.

## Initial preparation and startup

From the repository root, start an established FreeIPA installation with:

```sh
docker compose --env-file /dev/null up -d --wait ipa
```

For a fresh installation only, run `sudo python3 proxmox/atlas-freeipa.py initialize`
before startup. This creates the separate `pki_freeipa` internal intermediate
under the established Vault-held root. It never exports or resets that root. The
issuance role permits only `ipa.home.arpa`, using ML-DSA-44, and creates the shared
HTTP/LDAP certificate. Normal startup does not issue replacement identities.

Preparation writes `fullchain.pem`, `key.pem`, the public CA certificate
`ca.pem`, private `installer.json` passwords and `ipa-server-install-options`
under `docker/freeipa/secrets/`, excluded from Git. Compose
mounts the certificate inputs and protected options file alongside the persistent
`infra_ipa_data` volume. Existing installations and restored volumes use native
FreeIPA startup, without custom entrypoints, readiness checkpoints or volume
identity markers. Installation flags and mount paths are documented in Compose.

After the updated Atlas catalog is deployed and applied, Puppet's existing
`vault_cert` resource renews the PEM files. Its refresh command
imports the renewed certificate into both HTTP and Directory Server and restarts
the affected services. The Directory Manager password comes from
`secrets/installer.json`; no certificate JSON or custom renewal timer is needed.
See [the certificate lifecycle](openvox-infra.md#automated-vault-and-ipa-transport-renewal).

By default initialization generates and retains independent admin and Directory
Manager passwords. To supply declared credentials instead, pass
`--password-file /root/PROTECTED_INPUT.json`, containing `admin_password` and
`dm_password`. The input must belong to root and exclude group/other access.
Never put that file in Git.

Use Compose for ordinary startup and restoration, following the instructions in
the Compose file. The preparation helper is only for a fresh installation.

## Jellyfin TV identity

User `tv` is reserved for Jellyfin playback, with shell `/usr/sbin/nologin`,
home `/nonexistent`, and no membership in `ipausers` or privileged groups.
The live HBAC rules `allow_all` and `allow_systemd-user` use `ipausers` and
`admins` instead of `usercategory=all`, preserving existing users' host access
while excluding `tv`. A recreated `tv` must be removed from the automatic
`ipausers` group. Both Vault authentication setup scripts exclude `uid=tv`.
Its password and Jellyfin pairing instructions live in the Jellyfin stack.

## DNS and client selection

The router publishes `ipa.home.arpa` at Atlas's reserved IPv4 address and stable
IPv6 ULA. Atlas remains the canonical PTR. The DNS-only deployment and 53 smoke
checks passed; see [the measured DNS evidence](archive/migration/atlas-ipa-dns-results.json).

All managed clients, including both Proxmox hypervisors, use the shared
`ipa.home.arpa` server setting. Each node declares its enrollment hostname and
`/usr/local/share/ca-certificates/atlas-infrastructure.crt` as its authenticated
CA file. Install that trust before enrolling a new client.

Both Proxmox hypervisors completed this identity migration on 2026-10-22.
Fresh host credentials, LDAP/HTTP service tickets, IPA API access and Puppet's
enrollment check passed on both hosts. Their SSH public keys are registered
in the new IPA, normal SSH host verification works, and the protected SSH/PAM
configuration remained unchanged. SSSD and scheduled Puppet are running.

Changing the server setting does not migrate an existing machine identity.
The Puppet enrollment provider refuses mismatched existing configuration;
matching configuration alone does not prove that the host keytab works.
Moving an existing client from the retired authority therefore requires an
explicit administrator operation:

1. Preserve a root-only backup of its IPA, Kerberos and SSSD configuration,
   trust files and keytab. Retain the existing SSH/PAM configuration and any
   non-host keytab principals. Verify a root SSH connection using a trusted,
   explicitly pinned host public key so access does not depend on SSSD's cache.
2. Verify that each migrated host's SSH public keys are registered in its new
   IPA host entry. If missing, obtain `/etc/ssh/ssh_host_*_key.pub` through that
   verified connection and register them with `ipa host-mod <fqdn>
   --sshpubkey='<public-key>'`, repeating `--sshpubkey` for each public key.
   Include hosts reached through another migrating host. Complete this before
   clearing SSSD caches: imported host entries may lack SSH public keys, and
   cached trust can otherwise hide the missing records until access is lost.
3. Using an authorized account on `ipa.home.arpa`, issue fresh keys for the
   existing `host/<fqdn>@HOME.ARPA` identity into a separate private keytab with
   `ipa-getkeytab -s ipa.home.arpa -p host/<fqdn>@HOME.ARPA -k <new-keytab>`.
   Supply authenticated Atlas trust with `--cacert`; omitting `-r` creates new
   keys and invalidates previous keys for that host principal.
4. Verify the new keytab with `kinit -k -t <new-keytab> host/<fqdn>@HOME.ARPA`,
   using a temporary Kerberos configuration that explicitly selects
   `ipa.home.arpa`. Install the authenticated CA trust, update IPA, Kerberos
   and SSSD server settings, and replace the old host keys while preserving
   any other principals. Run `ipa-certupdate` to refresh the native IPA,
   Kerberos and system CA stores, and verify the resulting certificate bundles.
   Restart SSSD and clear its old identity cache.
5. Verify host-keytab authentication through the normal client configuration,
   SSSD user/group lookup, a fresh SSH connection and the Puppet enrollment
   check before resuming scheduled Puppet runs.

Do not use client uninstall as a keytab repair: it removes every principal in
the configured realm from `/etc/krb5.keytab` and restores earlier SSH, PAM and
other client configuration.

## Established identities and recovery

The completed account migration preserved the original users, numeric IDs,
memberships and policies. See [migration evidence](archive/migration/atlas-ipa-migration-results.json)
for the measured acceptance. Migration-only import and disposable clone tools
are retired. Native startup reads the existing `/data` volume; it does not
repeat that migration.

Recover the established volume and Compose-adjacent `secrets/` with the normal
[Atlas backup workflow](atlas-recovery.md). Check trusted HTTPS/LDAPS,
administrator Kerberos/API access and required user access after restoration.
FreeIPA remains CA-less, with PKINIT explicitly disabled and no Dogtag instance.

## Focused checks

```bash
uv run --frozen pytest -q tests/test_atlas_freeipa.py tests/test_native_compose_sources.py
```

Long builds and live installations in the main Codex thread use `wake-run`.
