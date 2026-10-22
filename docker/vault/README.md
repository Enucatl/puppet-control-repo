# Vault FreeIPA SSO

Run once on an enrolled FreeIPA client with an IPA administrator ticket and
an authenticated Vault administrator context:

```sh
export VAULT_ADDR=https://vault.home.arpa:8200
bash docker/vault/scripts/11-vault-kerberos.sh
```

The script requires `ipa`, `ipa-getkeytab`, `klist`, `vault`, and `jq`. It creates
`HTTP/vault.home.arpa@HOME.ARPA` without enrolling a separate Vault host,
permits the IPA `admins` group to retrieve its keys, and configures Vault's
Kerberos auth method. Reruns retrieve existing keys without rotating them.
Temporary keytabs and bind credentials use a private directory and are deleted
on exit. LDAP must already be configured by `10-vault-ldap.sh` and its admin
group policy by `13-vault-admin-policy.sh`.

Only members of FreeIPA `admins` receive the existing LDAP admin-group policies.
Other users receive Vault's default policy. Tokens last eight hours, with a
24-hour maximum. The Kerberos method does not renew tokens; run
`vault-sso-login` again after expiry or open a new login shell.

`profile::vault_sso` installs `/usr/local/bin/vault-sso-login` and
`/etc/profile.d/vault-sso.sh` on Atlas and Aperture. Interactive login shells
exchange an available Kerberos ticket for a Vault token; existing valid tokens
and explicit `VAULT_TOKEN` values are preserved. Missing tickets and login
failures do not block shell access. Noninteractive SSH commands do not log in
automatically. The helper can also be run directly.

SSH must authenticate with GSSAPI and delegate credentials. Check `klist` on
Atlas after connecting from Aperture. After initial setup, an older cached
Vault service ticket may need replacing; reconnect from Aperture to obtain a
fresh delegated cache.

Vault's bundled gokrb5 version has an
[upstream SHA2 PAC parsing bug](https://github.com/jcmturner/gokrb5/pull/493).
The script preserves existing service ticket flags and adds
`NO_AUTH_DATA_REQUIRED` only to the Vault principal. This omits PAC and
authentication-indicator data; Vault obtains authorization groups through
verified LDAP instead. Encryption and service keys remain unchanged. Remove
this workaround when Vault incorporates the upstream fix; this setup does not
enforce Kerberos authentication indicators such as an MFA requirement.
