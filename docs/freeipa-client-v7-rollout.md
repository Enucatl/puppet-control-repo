# FreeIPA client v7 rollout

1. Run `docker/freeipa/bootstrap-puppet-enroller.sh` inside the FreeIPA server
   container with an admin ticket and authenticated Vault environment. This
   creates the least-privilege account/role and stores its password as
   `kv/puppet:freeipa-client-enrollment-password`. Keep the distinct
   `freeipa-admin-password` field only for the `freeipa_users` class.
2. Before deployment, inspect every client `/etc/ipa/default.conf`. Its
   normalized `domain` must be `home.arpa` and `server` must be
   `freeipa.home.arpa`; repair mismatches manually.
3. Deploy to one already-enrolled Ubuntu canary. Require a successful,
   zero-change Puppet run.
4. Enroll a disposable new Ubuntu VM and verify `/etc/ipa/default.conf`,
   `/etc/krb5.keytab`, `getent passwd` for an IPA identity, running/enabled
   SSSD, and a zero-change second Puppet run.
5. Roll out to the remaining supported clients. Then rotate the enroller
   password with `ROTATE_ENROLLER_PASSWORD=1` and remove the former enrollment
   field from Vault.

Version 7 intentionally fails on non-Ubuntu and unsupported Ubuntu releases.
