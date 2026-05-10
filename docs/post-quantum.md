# Post-Quantum Cryptography Remediation

This repository still relies on classical asymmetric cryptography for its
infrastructure identities, key exchange, and VPN access. In light of Filippo
Valsorda's April 2026 article, [A Cryptography Engineer's Perspective on
Quantum Computing Timelines](https://words.filippo.io/crqc-timeline/), treat
RSA, ECDSA, Ed25519, and X25519 as migration risks rather than long-term
security foundations.

The operational takeaway is not to increase symmetric key sizes. The urgent
work is to reduce dependence on classical public-key cryptography, especially
for long-lived trust anchors, authentication roots, and traffic that is exposed
to store-now-decrypt-later collection.

## Priority Remediations

1. Replace or constrain long-lived classical PKI.

   Vault PKI currently provisions a 10-year root CA, a 5-year intermediate CA,
   and a long-lived Vault TLS certificate in `docker/vault/scripts/config.sh`.
   Those lifetimes extend beyond the 2029 risk horizon discussed in the article.

   Remediate by reducing new classical CA and certificate lifetimes, avoiding
   new long-lived RSA/ECC roots, and tracking Vault/Puppet support for ML-DSA
   backed X.509 or a separate post-quantum identity layer.

2. Stop treating the RSA Puppet CA as a long-term identity root.

   `docker/vault/scripts/03-puppet-external-ca.sh` generates the Puppet CA with
   `openssl genrsa 4096` and imports it into Puppet. RSA-4096 is still strong
   against classical attackers, but it is not quantum-resistant.

   Remediate by documenting the Puppet CA as classical-only, shortening its
   validity, planning re-enrollment, and avoiding new high-value authorization
   paths that depend solely on Puppet certificates.

3. Reduce Vault cert-auth dependence on Puppet's classical CA.

   Vault cert auth is configured to trust the Puppet CA for the `puppet` role.
   If that CA became forgeable, Vault token issuance would depend mostly on the
   allowed DNS SAN policy.

   Remediate by keeping Vault token TTLs short, narrowing the cert-auth SAN
   allowlist, limiting policies exposed through this auth path, alerting on
   cert-auth use, and preparing a non-X.509 or post-quantum-capable auth path
   when one is available.

4. Prefer post-quantum SSH key exchange where supported.

   The Puppet data pins SSH host authentication to Ed25519 and does not
   explicitly prefer OpenSSH hybrid post-quantum key exchange algorithms.
   Ed25519 authentication remains classical, but SSH session key exchange can
   be improved where clients and servers support it.

   Remediate by configuring `KexAlgorithms` to prefer OpenSSH's hybrid
   post-quantum algorithms, while retaining fallbacks only where needed for
   compatibility.

5. Treat WireGuard as non-post-quantum.

   The VyOS router templates configure WireGuard peers and install a server
   private key. WireGuard uses X25519 and does not provide post-quantum key
   exchange.

   Remediate by not relying on WireGuard alone for long-lived sensitive
   confidentiality. Use post-quantum-capable application or file encryption for
   data with a long secrecy lifetime, and track a post-quantum VPN replacement
   or wrapper.

6. Do not prioritize symmetric-only changes.

   The Ansible Vault file uses AES256. Based on the article's guidance, this is
   not the urgent migration target. The repository's post-quantum remediation
   effort should focus on asymmetric authentication, signatures, and key
   exchange.

## Related Non-CRQC Hardening

- `docker/vault/config/vault-conf-insecure.hcl` disables Vault TLS. Keep this
  file out of normal operational paths, or remove it if it is no longer needed.
- `docker/vault/scripts/03-puppet-external-ca.sh` uses `curl --insecure` during
  bootstrap. Keep this limited to controlled bootstrap use and document that
  boundary if the workflow remains necessary.
