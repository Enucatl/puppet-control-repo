# FreeIPA provisioning on Atlas

`freeipa_users` only manages local group membership and removes the retired
`/run/puppet-ipa-admin-pass` file. `freeipa_users::provision` and its users are
included by `data/nodes/atlas.home.arpa.yaml`. The class requires Puppet's authenticated
remote certname to equal `atlas.home.arpa`, regardless of hostname/role facts.

Run `docker/freeipa/bootstrap-puppet-provisioner.sh` as root on that host. Atlas's
current IPA administrator credential is in the protected installer state, so
bootstrap without passing passwords in arguments, environment, or output:

```sh
sudo bash -c 'set -euo pipefail; jq -er .admin_password /root/openvox-infra/freeipa/installer.json | /opt/docker/puppet-control-repo/docker/freeipa/bootstrap-puppet-provisioner.sh --admin-password-stdin'
```

It uses a private temporary administrator ticket
to create `puppet-provisioner/atlas.home.arpa@HOME.ARPA`, a dedicated role and
privilege, and `/etc/puppet-ipa-provisioner.keytab` (root, `0400`). Reruns retain
working keys and remove Docker's provisioner from the delegated role if present.
The keytab helper uses `ipa.home.arpa`, Atlas's current authority. With no
arguments, the bootstrap retains its administrative Vault CLI credential path:
`kv/freeipa-admin:admin_password`, or the old shared field before retirement.
Atlas's Vault TLS requires the native runtime used by `proxmox/atlas-infra.py`;
do not use an incompatible Vault CLI to obtain its administrator credential.

The role can add users, read the required user/private-group information, and
manage membership of the default `ipausers` group. Key creation/retrieval is
restricted to `backrest`; it cannot administer existing users or privileged
groups. Ordinary Puppet runs use only the service keytab, with no administrator
fallback. Credential contents never enter the provisioning catalog.

The helper authenticates with private temporary caches and preserves working
keytabs, including `/etc/krb5-backrest.keytab`. Missing/stale local keytabs use
`ipa-getkeytab -r` when the principal already has keys. Only an explicit
`has_keytab: FALSE` permits key generation; lookup/authentication errors fail.
FreeIPA documents the underlying scoped operations in
[Keytab Retrieval](https://www.freeipa.org/page/V4/Keytab_Retrieval).

`docker/freeipa/retire-shared-admin-password.sh` is retained for the legacy
Docker authority only; do not run it on Atlas. Atlas has independent installer
credentials and no dependency on those retired shared fields. The legacy script
requires administrative Vault access and verifies that ordinary Puppet certificate authentication
cannot read `kv/freeipa-admin`, saves independent `admin_password` and
`directory_manager_password` values there, rotates and verifies each account,
and removes the obsolete installation password from the FreeIPA container's
environment (recreating that container). It then removes exactly the four
retired shared fields with a version-checked Vault update and verifies that
unrelated fields, including `freeipa::client::password`, remain unchanged.
Reruns recover staged credentials after an interruption. Vault version history
is retained; the old passwords no longer authenticate either rotated account.

Broader Vault isolation is a separate change. The shared Puppet entry still
contains credentials for router provisioning, Vault LDAP, Samba, GeoIP, and
other applications. In particular, globally loaded GeoIP credentials deserve
the same catalog-exposure review. Vault access is authorized by path; ordinary
agents do not need direct access to the whole shared entry for compiler-side
Hiera lookups.

Checks: `bash tests/test_freeipa_provision.sh` and
`uv run pytest tests/test_profile_catalogs.py`. Follow `wake-run` for long
operations in the main agent thread.
