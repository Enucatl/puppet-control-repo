# FreeIPA provisioning on Docker

`freeipa_users` only manages local group membership and removes the retired
`/run/puppet-ipa-admin-pass` file. `freeipa_users::provision` and its users are
included by `data/nodes/docker.home.arpa.yaml`. The class requires Puppet's authenticated
remote certname to equal `docker.home.arpa`, regardless of hostname/role facts.

Run `docker/freeipa/bootstrap-puppet-provisioner.sh` as root on that host with
administrative Vault access. It uses a private temporary administrator ticket
to create `puppet-provisioner/docker.home.arpa@HOME.ARPA`, a dedicated role and
privilege, and `/etc/puppet-ipa-provisioner.keytab` (root, `0400`). Reruns retain
working keys. The bootstrap reads `kv/freeipa-admin:admin_password` after the
migration, or the old shared field before retirement.

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

After deployment and delegated provisioning verification, run
`docker/freeipa/retire-shared-admin-password.sh` as root with administrative
Vault access. It verifies that ordinary Puppet certificate authentication
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
