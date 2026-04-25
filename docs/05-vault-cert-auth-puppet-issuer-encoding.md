# Vault Cert Auth Fails for Proxmox Puppet Certificate

Date: 2026-04-25

## Summary

`proxmox.home.arpa` cannot authenticate to Vault through the generic Puppet
certificate auth role:

```sh
vault login -method=cert name=puppet
```

The same host previously authenticated through a temporary dedicated
leaf-certificate role.

The Vault policy is not the problem. The `allowed_dns_sans="*.home.arpa"`
constraint is also not the problem. The proxmox certificate's SAN matches that
pattern.

The failure happens earlier: Vault uses Go `crypto/x509` to build a certificate
chain for CA-backed cert auth roles. Go cannot build a chain from the proxmox
leaf certificate to the configured Puppet CA because the leaf certificate's raw
issuer DN encoding does not byte-match the Puppet CA certificate's raw subject
DN encoding.

## Current Vault State

The intended generic role is:

```text
auth/cert/certs/puppet
certificate: Puppet CA chain
allowed_dns_sans: ["*.home.arpa"]
token_policies: ["puppet"]
token_ttl: 900
```

The workaround role was a leaf-pinned `auth/cert/certs/...` entry for
`proxmox.home.arpa` with no extra constraints.

The workaround worked because it pinned the exact proxmox leaf certificate and
did not rely on Go building a chain from the leaf to the Puppet CA. That role
has now been retired.

## Certificate Comparison

Both certificates are valid TLS client certificates issued by a textual
`CN=Puppet CA`.

`docker.home.arpa`:

```text
subject: CN=docker.home.arpa
issuer: CN=Puppet CA
SANs: DNS:docker.home.arpa, DNS:docker, DNS:docker.home.arpa, DNS:puppet
EKU: TLS Web Server Authentication, TLS Web Client Authentication
```

`proxmox.home.arpa`:

```text
subject: CN=proxmox.home.arpa
issuer: CN=Puppet CA
SANs: DNS:proxmox.home.arpa
EKU: TLS Web Server Authentication, TLS Web Client Authentication
```

OpenSSL accepts both when verifying against the exact CA bundle stored in
`auth/cert/certs/puppet`:

```text
proxmox.pem: OK
docker.pem: OK
```

## OpenSSL vs Go/Vault

The decisive difference is visible only by comparing the raw ASN.1 names.

```text
Puppet CA RawSubject:
30143112301006035504031309507570706574204341

docker.home.arpa RawIssuer:
30143112301006035504031309507570706574204341
RawIssuer == CA RawSubject: true

proxmox.home.arpa RawIssuer:
30143112301006035504030c09507570706574204341
RawIssuer == CA RawSubject: false
```

The only meaningful difference is the ASN.1 string type used for the common
name:

```text
13 = PrintableString
0c = UTF8String
```

So all three names render as `CN=Puppet CA`, but they are not byte-identical:

```text
Puppet CA subject:       CN=Puppet CA encoded as PrintableString
docker leaf issuer:      CN=Puppet CA encoded as PrintableString
proxmox leaf issuer:     CN=Puppet CA encoded as UTF8String
```

Go's `crypto/x509.Verify`, used by Vault cert auth, requires the issuer raw DN
to match the parent subject raw DN during chain construction. Because proxmox's
raw issuer differs from the Puppet CA raw subject, Go reports:

```text
x509.UnknownAuthorityError: x509: certificate signed by unknown authority
```

Vault then returns the generic cert auth error:

```text
failed to match all constraints for this login certificate
```

The SAN glob itself matches correctly:

```text
glob(*.home.arpa, proxmox.home.arpa)=true
```

## Likely Cause

The proxmox leaf was signed by a different certificate-signing implementation
or code path than the docker leaf, even if the operator-facing Puppet commands
looked the same.

This can happen when:

- The Puppet CA was imported from an external CA and different Puppet Server or
  Puppet gem/JVM/Bouncy Castle versions signed leaves at different times.
- The Puppet CA was imported more than once, or the CA service was migrated, and
  the signer reconstructed the issuer DN from text instead of copying the CA
  certificate's raw subject encoding.
- A CSR was signed with a different command path than the server's normal
  certificate issuance path.
- The Puppet Server version changed between the docker and proxmox certificate
  issuance dates.

The dates support a change in signing path or software version:

```text
docker.home.arpa certificate:
notBefore=Dec  1 12:01:10 2025 GMT
notAfter=Nov 28 12:01:11 2040 GMT
serial=01

proxmox.home.arpa certificate:
notBefore=Jan  8 09:48:24 2026 GMT
notAfter=Jan  8 09:48:24 2031 GMT
serial=0B99
```

The different validity periods are another indication that the two leaves were
not produced by an identical signing configuration, even though both were
ultimately signed with the same Puppet CA key.

## Remediation

Do not replace `allowed_dns_sans` with `allowed_common_names`. SAN matching is
working and is the correct modern constraint.

The durable fix is to reissue the proxmox Puppet certificate through a signing
path that preserves the Puppet CA subject encoding in the leaf issuer field.

### Preferred Path

Use the current Puppet Server CA service after verifying that it now issues
certificates whose raw issuer matches the Puppet CA raw subject.

On the Puppet server, inspect the active Puppet CA certificate:

```sh
sudo openssl x509 \
  -in /etc/puppetlabs/puppet/ssl/certs/ca.pem \
  -noout -subject -nameopt dump_der
```

Revoke and clean the old proxmox certificate on the Puppet server:

```sh
sudo /opt/puppetlabs/bin/puppetserver ca revoke --certname proxmox.home.arpa
sudo /opt/puppetlabs/bin/puppetserver ca clean --certname proxmox.home.arpa
```

On `proxmox.home.arpa`, remove only the Puppet agent SSL material:

```sh
systemctl stop puppet || true
rm -rf /etc/puppetlabs/puppet/ssl
/opt/puppetlabs/bin/puppet ssl bootstrap
```

If autosign does not sign it automatically, sign it on the Puppet server:

```sh
sudo /opt/puppetlabs/bin/puppetserver ca list
sudo /opt/puppetlabs/bin/puppetserver ca sign --certname proxmox.home.arpa
```

Then validate from `proxmox.home.arpa`:

```sh
VAULT_ADDR=https://hcv.home.arpa:8200 \
VAULT_CLIENT_CERT=/etc/puppetlabs/puppet/ssl/certs/proxmox.home.arpa.pem \
VAULT_CLIENT_KEY=/etc/puppetlabs/puppet/ssl/private_keys/proxmox.home.arpa.pem \
vault login -method=cert -no-store name=puppet
```

If this succeeds, the temporary workaround role can be deleted from Vault if it
still exists.

### If Reissue Still Produces UTF8String Issuer

If Puppet Server continues to issue leaves with a UTF8String issuer while the
CA subject is PrintableString, Go/Vault will keep rejecting CA-backed auth for
those leaves.

In that case, regenerating only the proxmox leaf certificate is not enough. The
active Puppet Server signing path is systematically producing a raw issuer DN
that does not match the current CA raw subject.

Because this environment must keep Vault PKI, the Puppet CA rotation must be
done in a way that Vault produces a CA subject encoding that matches Puppet
Server's emitted leaf issuer encoding.

The current rotation script does not guarantee that.

Current script excerpt:

```sh
openssl req -new \
  -key ~/Downloads/puppet_ca_key.pem \
  -out ~/Downloads/puppet_ca.csr \
  -subj "/CN=Puppet CA: ${DOCKER_FQDN}"

vault write -format=json pki_int/root/sign-intermediate \
  csr=@"$HOME/Downloads/puppet_ca.csr" \
  format=pem_bundle \
  ttl="$INTERMEDIATE_CA_TTL" \
  common_name="Puppet CA"
```

Testing showed that Vault PKI encodes the exact common name `Puppet CA` as
PrintableString, even if the CSR subject is UTF8String and even when
`use_csr_values=true` is used. That means rotating with the current script can
recreate the same mismatch if Puppet Server continues to emit UTF8String issuer
DNs in leaf certificates.

Observed non-importing Vault PKI test:

```text
Vault-signed CA subject CN=Puppet CA:
PRINTABLESTRING :Puppet CA
```

By contrast, the current Puppet Server leaf issuer for proxmox is:

```text
UTF8STRING :Puppet CA
```

Vault PKI can be forced to use UTF8String for the CA common name by choosing a
name that is not representable as PrintableString. For example `_` forces
UTF8String:

```text
common_name="Puppet_CA"
=> UTF8STRING :Puppet_CA
```

If using Vault PKI for the rotated Puppet CA, prefer a UTF8-forcing CA common
name such as `Puppet_CA`, then validate a throwaway leaf before migrating real
agents.

Required verification gate:

1. Generate the candidate Puppet CA through Vault PKI.
2. Import it into a test Puppet Server CA state or otherwise issue a throwaway
   leaf using the same Puppet Server signer that production will use.
3. Compare the throwaway leaf raw issuer DN against the candidate CA raw
   subject DN.
4. Proceed only if `leaf.RawIssuer == ca.RawSubject`.

If the raw issuer and raw subject still differ, stop the rotation. It will not
fix Vault cert auth.

High-level sequence:

1. Generate or obtain a new Puppet CA certificate through Vault PKI with a
   UTF8String-forcing subject, for example `CN=Puppet_CA`.
2. Import it into Puppet Server with `puppetserver ca import`.
3. Issue a throwaway leaf and verify `leaf.RawIssuer == ca.RawSubject`.
4. Reissue Puppet certificates for all agents.
5. Update the Vault `auth/cert/certs/puppet` role with the new Puppet CA chain.
6. Remove any per-leaf workaround roles after every node authenticates through
   `name=puppet`.

This should be scheduled as a CA rotation because all existing Puppet agent
trust stores and certificates are affected.

## Planned Rotation Runbook

The goal is to keep Vault PKI, rotate the Puppet CA to a subject encoding that
matches Puppet Server's leaf issuer encoding, and restore the single generic
Vault cert auth role:

```text
auth/cert/certs/puppet
```

The updated rotation script is:

```text
docker/vault/scripts/03-puppet-external-ca.sh
```

It now defaults the Puppet CA common name to:

```text
CN=Puppet_CA
```

The underscore is intentional. It forces Vault PKI to encode the CA common name
as UTF8String, which should match current Puppet Server leaf issuer encoding.

### What the Script Does

When run on `docker.home.arpa`, the script:

1. Fetches the Vault root/intermediate CA certificates and refreshes local trust.
2. Generates a new Puppet CA private key and CSR.
3. Asks Vault PKI to sign the Puppet CA as an intermediate with
   `common_name="$PUPPET_CA_COMMON_NAME"`.
4. Rotates/downloads Vault PKI CRLs.
5. Backs up the current Puppet SSL directory to:

   ```text
   ~/Downloads/puppet-ssl-<timestamp>.tar.gz
   ```

6. Imports the new Puppet CA into Puppet Server using:

   ```sh
   puppetserver ca import
   ```

7. Restarts `puppetserver` by default.
8. Generates a throwaway Puppet certificate:

   ```text
   puppet-ca-encoding-test.home.arpa
   ```

9. Verifies the critical DER equality:

   ```text
   generated_leaf.RawIssuer == puppet_ca.RawSubject
   ```

10. Updates the generic Vault cert auth role:

    ```text
    auth/cert/certs/puppet
    ```

11. Verifies Vault cert auth with the throwaway certificate.
12. Cleans the throwaway certificate.

If the DER check or Vault cert-auth check fails, the script exits non-zero. Do
not continue with agent rotation if that happens.

### Before Running

Run this from `docker.home.arpa`, not from a workstation:

```sh
cd /opt/docker/puppet-control-repo
git status --short
```

Confirm the updated repo is deployed on `docker.home.arpa` and the script has
the new verification functions.

Confirm required environment variables are available. At minimum:

```sh
echo "$VAULT_ADDR"
vault status
vault token lookup
```

The Vault token must be able to:

- Sign the Puppet CA CSR using `pki_int/root/sign-intermediate`.
- Update `auth/cert/certs/puppet`.
- Read/update enough Vault state for the script's existing PKI operations.

Confirm Puppet Server is healthy before changing it:

```sh
sudo systemctl status puppetserver --no-pager
sudo /opt/puppetlabs/bin/puppetserver ca list --all
```

Optional dry-read checks:

```sh
sudo openssl x509 \
  -in /etc/puppetlabs/puppet/ssl/certs/ca.pem \
  -noout -subject -issuer -serial -dates -fingerprint -sha256

vault read auth/cert/certs/puppet
```

### Run the Rotation

Run:

```sh
cd /opt/docker/puppet-control-repo
docker/vault/scripts/03-puppet-external-ca.sh
```

Defaults used by the script:

```sh
PUPPET_CA_COMMON_NAME=Puppet_CA
PUPPET_ENCODING_TEST_CERTNAME=puppet-ca-encoding-test.home.arpa
PUPPET_CERT_AUTH_ROLE=puppet
RESTART_PUPPETSERVER_AFTER_IMPORT=true
BACKUP_PUPPET_SSL_DIR=true
```

Only override these if there is a specific reason.

Expected successful output includes:

```text
Puppet CA subject DER: ...
Test leaf issuer DER: ...
```

The two DER strings must be identical. The script enforces this.

The script also tests:

```sh
vault login -method=cert name=puppet
```

using the generated throwaway certificate.

### If the Script Fails

If the script fails before import, do not continue. Fix the reported error and
rerun.

If the script fails after import but before successful validation:

1. Do not rotate agents.
2. Check the backup path printed by the script:

   ```text
   ~/Downloads/puppet-ssl-<timestamp>.tar.gz
   ```

3. Inspect Puppet Server logs:

   ```sh
   sudo journalctl -u puppetserver --since "30 minutes ago" --no-pager
   sudo tail -n 300 /var/log/puppetlabs/puppetserver/puppetserver.log
   ```

4. Decide whether to restore the SSL directory backup or debug the new CA state.

### Manual Steps After Successful Script Run

The script does not rotate real agents. That remains manual/operational.

For each Puppet agent, remove its old SSL material and bootstrap a new
certificate. Example for `proxmox.home.arpa`:

```sh
sudo systemctl stop puppet || true
sudo rm -rf /etc/puppetlabs/puppet/ssl
sudo /opt/puppetlabs/bin/puppet ssl bootstrap
sudo /opt/puppetlabs/bin/puppet agent --test
sudo systemctl start puppet
```

If autosign needs a fresh Vault token, regenerate the CSR challenge password as
described in the enrollment scripts before running `puppet ssl bootstrap`.

After each agent receives a new cert, verify Vault cert auth on that host:

```sh
VAULT_ADDR=https://hcv.home.arpa:8200 \
VAULT_CLIENT_CERT=/etc/puppetlabs/puppet/ssl/certs/$(hostname -f).pem \
VAULT_CLIENT_KEY=/etc/puppetlabs/puppet/ssl/private_keys/$(hostname -f).pem \
vault login -method=cert -no-store name=puppet
```

For `proxmox.home.arpa`, explicitly verify that the workaround is no longer
needed:

```sh
VAULT_ADDR=https://hcv.home.arpa:8200 \
VAULT_CLIENT_CERT=/etc/puppetlabs/puppet/ssl/certs/proxmox.home.arpa.pem \
VAULT_CLIENT_KEY=/etc/puppetlabs/puppet/ssl/private_keys/proxmox.home.arpa.pem \
vault login -method=cert -no-store name=puppet
```

Then the dedicated workaround role can be deleted from Vault if it still
exists.

### Post-Rotation Checks

On `docker.home.arpa`:

```sh
sudo /opt/puppetlabs/bin/puppetserver ca list --all
vault read auth/cert/certs/puppet
sudo journalctl -u puppetserver --since "1 hour ago" --no-pager
```

On each rotated agent:

```sh
sudo /opt/puppetlabs/bin/puppet agent --test
```

Check that Hiera/Vault lookups work during a Puppet run and that no agent is
still relying on a per-leaf Vault cert auth role.

### Puppet Server Signer Investigation

The alternative to changing the Puppet CA subject is to investigate why Puppet
Server emits `CN=Puppet CA` as UTF8String in leaf issuers even though the CA
certificate's raw subject uses PrintableString.

Start on the Puppet server:

```sh
sudo dpkg -l | grep -E 'puppetserver|puppet-agent|puppetserver-ca'
sudo /opt/puppetlabs/bin/puppetserver --version
sudo /opt/puppetlabs/bin/puppet --version
```

The relevant installed code is usually under:

```text
/opt/puppetlabs/server/apps/puppetserver/
/opt/puppetlabs/server/data/puppetserver/
/opt/puppetlabs/puppet/lib/ruby/vendor_gems/gems/
```

Search for the certificate signing implementation:

```sh
sudo grep -R "issuer" \
  /opt/puppetlabs/server/apps/puppetserver \
  /opt/puppetlabs/server/data/puppetserver \
  /opt/puppetlabs/puppet/lib/ruby/vendor_gems/gems \
  | grep -Ei "x509|cert|subject|issuer" | head -100
```

Also search for Bouncy Castle usage, because Puppet Server is a JVM service and
certificate generation is commonly implemented through JVM crypto classes:

```sh
sudo grep -R "X509v3CertificateBuilder\|JcaX509v3CertificateBuilder\|X500Name\|BCStyle" \
  /opt/puppetlabs/server/apps/puppetserver \
  /opt/puppetlabs/server/data/puppetserver \
  2>/dev/null | head -100
```

The behavior to look for is whether the signer:

- Copies the CA certificate issuer/subject as raw encoded ASN.1.
- Reconstructs the issuer DN from parsed fields or a string such as
  `CN=Puppet CA`.

If it reconstructs the issuer DN, it may choose UTF8String for the leaf issuer
even when the CA certificate subject used PrintableString. That is the observed
failure mode.

This is likely Puppet Server or Puppet Server CA implementation behavior rather
than a configuration setting, so expect the investigation to lead to a version
comparison, upstream bug report, or patch rather than a simple `puppet.conf`
knob.

## Verification Commands Used

Compare the leaf certificates:

```sh
ssh docker.home.arpa \
  'openssl x509 -in /etc/puppetlabs/puppet/ssl/certs/docker.home.arpa.pem -noout -text'

ssh proxmox.home.arpa \
  'openssl x509 -in /etc/puppetlabs/puppet/ssl/certs/proxmox.home.arpa.pem -noout -text'
```

Verify both leaves with OpenSSL against the Vault cert auth CA bundle:

```sh
vault read -field=certificate auth/cert/certs/puppet > /tmp/puppet-role-chain.pem
openssl verify -CAfile /tmp/puppet-role-chain.pem /tmp/proxmox.pem /tmp/docker.pem
```

Reproduce Vault's Go verification behavior:

```go
cert.Verify(x509.VerifyOptions{
    Roots: roots,
    KeyUsages: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth},
})
```

Observed result:

```text
proxmox.pem: x509.UnknownAuthorityError
docker.pem: verify ok
```
