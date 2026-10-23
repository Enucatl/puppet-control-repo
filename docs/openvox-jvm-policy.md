# JVM algorithm-policy limitation and compensating controls (#12)

The accepted image preserves the maintained JDK algorithm-policy properties,
but BCJSSE does not implement every expression in those properties. Preserving
the text is necessary; it does not prove that every provider enforces it.

The BC parser accepts unconditional algorithm names and individual `keySize`
constraints. It ignores unsupported entries with a warning, including `usage`,
`jdkCA`, `denyAfter`, compound `&` constraints and `include` directives. There
is no configuration flag in that parser to turn these unsupported expressions
into startup failures. This is provider code behavior, not a malformed OpenVox
configuration. The matching upstream implementation is
[BC's disabled-algorithm parser](https://github.com/bcgit/bc-java/blob/r1rv86/tls/src/main/java/org/bouncycastle/jsse/provider/DisabledAlgorithmConstraints.java).

The accepted runtime emits warnings for three SHA-1 handshake-signature
conditions and two SHA-1 certificate-path conditions inherited from OpenJDK
25. The latter include a restriction scoped to JDK-distributed roots and a
signed-JAR date condition. These warnings must remain visible; deleting the
maintained restrictions to silence them would hide the limitation.

## Deployment policy

Keep the single-equals append form:

```text
-Djava.security.properties=/opt/puppetlabs/server/data/puppetserver/java.security.mldsa
```

Do not use the double-equals form, replace `jdk.tls.disabledAlgorithms` or
`jdk.certpath.disabledAlgorithms` with a short local list, or assume a textual
property append repairs BC's parser. The current configuration retains those
maintained restrictions and independently narrows the OpenVox transport:

| Boundary | Compensating control |
| --- | --- |
| Negotiated protocol | Listener and Java outbound client use TLS 1.3 only |
| Key exchange | JVM named groups contain only `X25519MLKEM768` |
| CertificateVerify signatures | Both JVM client and server signature lists contain only `mldsa44` |
| Certificate signatures | Both BC client/server `SignatureSchemesCert` lists contain only `mldsa44` |
| Trust and issuance | Explicit offline-root hierarchy, separately scoped intermediates, authenticated root pin and ML-DSA-only import validation |

Certificate signatures and CertificateVerify are separate checks. An ML-DSA
leaf could otherwise be signed by an RSA intermediate; restricting the
handshake signature alone would not establish an all-ML-DSA certificate chain.
BC applies the configured certificate-signature constraints through its
[certificate-chain algorithm checker](https://github.com/bcgit/bc-java/blob/r1rv86/tls/src/main/java/org/bouncycastle/jsse/provider/ProvAlgorithmChecker.java),
and exposes separate
[signature and certificate-signature parameters](https://github.com/bcgit/bc-java/blob/r1rv86/tls/src/main/java/org/bouncycastle/jsse/BCSSLParameters.java).

These controls address the configured private-PKI transport. They do not add
general support for conditional JDK policy to BCJSSE, change signed-JAR policy,
or cover arbitrary new Java TLS contexts that omit the deployment settings.
The Vault exception permits classical-only clients at Vault's listener;
OpenVox's configured JVM client still requires the hybrid group.

## Reproducible runtime evidence

`docker/openvox/infra-jvm-policy-check.rb` uses the accepted image's actual
JRuby, BC classes and JDK. It creates a temporary test hierarchy with keys held
only in process memory. The test certificates include a valid all-ML-DSA chain
and a valid mixed chain whose ML-DSA leaf is signed using RSA/SHA-1.

Ten checks passed in a disposable container with no network, a read-only root
filesystem and temporary memory-backed storage. The check establishes that:

- Maintained TLS/certificate-path restriction strings remain loaded and are
  unchanged by the test. Supported unconditional MD5 and RSA key-size
  restrictions reject their negative fixtures.
- BC's parser ignores the conditional SHA-1 expression, while a supported
  unconditional SHA-1 restriction rejects the same algorithm. The test uses a
  separate scratch property and never replaces maintained properties.
- The actual BC chain algorithm checker accepts the valid ML-DSA chain and
  rejects the mixed RSA/SHA-1 chain when supplied the deployment's ML-DSA-only
  certificate-signature constraint. The stock private-root checker accepts
  that mixed fixture; this alone is not evidence that SunJSSE would reject it,
  because the stock `jdkCA` condition applies to JDK-distributed trust anchors.

The self-check constructs the constrained checker directly. It does not prove
that a particular HTTP client has applied those parameters, nor perform a TLS
handshake. The end-to-end Java/JRuby transport checks remain separate evidence.
Reflection accesses provider internals only in this self-check, never in
production startup.

Run locally using a readable staging directory if the checkout is NFS-backed:

```sh
infra_policy_stage=$(mktemp -d /tmp/openvox-jvm-public.XXXXXX)
chmod 0755 "$infra_policy_stage"
cp docker/openvox/infra-jvm-policy-check.rb docker/openvox/openssl.cnf "$infra_policy_stage/"
chmod 0644 "$infra_policy_stage/"*
docker --host unix:///var/run/docker.sock run --rm --network none --read-only \
  --cap-drop ALL --security-opt no-new-privileges:true --user 999:999 \
  --tmpfs /tmp:rw,nosuid,nodev,size=128m,uid=999,gid=999 \
  -e OPENSSL_CONF=/infra/openssl.cnf -v "$infra_policy_stage:/infra:ro" \
  --entrypoint /opt/puppetlabs/puppet/bin/ruby \
  ghcr.io/enucatl/openvox-server@sha256:5659cb80fcd29b22b678fe761ccdc4a63777dd738e8dce77a8aad16ac1e3cf6a \
  /infra/infra-jvm-policy-check.rb
rm -r "$infra_policy_stage"
```

The remaining provider gate is exact conditional-policy semantics across all
JVM consumers. Fixing that requires upstream/provider changes and their own
negative tests. Before widening certificate algorithms or using public JDK
trust stores, re-evaluate the skipped restrictions and test the affected real
consumer. This self-check does not close that broader gate.
