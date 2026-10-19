#!/opt/puppetlabs/puppet/bin/ruby

# Isolated accepted-image self-check; all test keys stay in process memory.
if ARGV.first != '--jvm'
  require 'openssl'
  require 'tmpdir'
  File.umask(0o077)
  directory = Dir.mktmpdir('openvox-jvm-policy-')
  root_key = OpenSSL::PKey.generate_key('ML-DSA-44')
  rsa_key = OpenSSL::PKey::RSA.new(2048)
  leaf_key = OpenSSL::PKey.generate_key('ML-DSA-44')
  make_certificate = lambda do |name, key, issuer, issuer_key, authority, digest|
    cert = OpenSSL::X509::Certificate.new
    cert.version = 2
    cert.serial = name.bytes.sum
    cert.subject = OpenSSL::X509::Name.parse("/CN=#{name}")
    cert.issuer = issuer ? issuer.subject : cert.subject
    cert.public_key = key
    cert.not_before = Time.now - 60
    cert.not_after = Time.now + 3600
    factory = OpenSSL::X509::ExtensionFactory.new
    factory.subject_certificate = cert
    factory.issuer_certificate = issuer || cert
    cert.add_extension(factory.create_extension('basicConstraints', authority ? 'CA:TRUE' : 'CA:FALSE', true))
    cert.add_extension(factory.create_extension('keyUsage', authority ? 'keyCertSign,cRLSign' : 'digitalSignature', true))
    cert.add_extension(factory.create_extension('extendedKeyUsage', 'serverAuth')) unless authority
    cert.sign(issuer_key, digest)
    raise 'Invalid test certificate signature' unless cert.verify(issuer_key)
    cert
  end
  root = make_certificate.call('Test MLDSA root', root_key, nil, root_key, true, nil)
  intermediate = make_certificate.call('Test RSA intermediate', rsa_key, root, root_key, true, nil)
  valid_leaf = make_certificate.call('Test MLDSA leaf', leaf_key, root, root_key, false, nil)
  sha1_leaf = make_certificate.call('Test SHA1 leaf', leaf_key, intermediate, rsa_key, false, 'SHA1')
  { root: root, intermediate: intermediate, valid: valid_leaf, sha1: sha1_leaf }.each do |name, cert|
    File.write("#{directory}/#{name}.pem", cert.to_pem)
  end
  exec('java', '-Djava.security.properties=/opt/puppetlabs/server/data/puppetserver/java.security.mldsa',
       '-Djdk.tls.namedGroups=X25519MLKEM768', '-Djdk.tls.client.SignatureSchemes=mldsa44',
       '-Dorg.bouncycastle.jsse.client.SignatureSchemesCert=mldsa44',
       '-cp', '/opt/puppetlabs/server/apps/puppetserver/puppet-server-release.jar',
       'org.jruby.Main', __FILE__, '--jvm', directory)
end

require 'java'
require 'json'
java_import java.security.Security
java_import java.util.EnumSet
java_import org.bouncycastle.jsse.java.security.BCCryptoPrimitive

def invoke(method, target, *arguments)
  method.accessible = true
  converted = arguments.map { |arg| arg.is_a?(String) ? arg.to_java(:string) : arg }
  method.invoke(target, *converted)
end

checks = []
record = lambda do |name, passed|
  raise "Policy check failed: #{name}" unless passed
  checks << name
end
stock_tls = Security.get_property('jdk.tls.disabledAlgorithms')
stock_certpath = Security.get_property('jdk.certpath.disabledAlgorithms')
record.call('maintained stock TLS restrictions remain loaded', stock_tls.include?('MD5withRSA') && stock_tls.include?('usage HandshakeSignature'))
record.call('maintained stock certpath restrictions remain loaded', stock_certpath.include?('SHA1 jdkCA & usage TLSServer'))

decomposer_class = java.lang.Class.for_name('org.bouncycastle.jsse.provider.ProvAlgorithmDecomposer')
field = decomposer_class.get_declared_field('INSTANCE_X509')
field.accessible = true
decomposer = field.get(nil)
disabled_class = java.lang.Class.for_name('org.bouncycastle.jsse.provider.DisabledAlgorithmConstraints')
create = disabled_class.declared_methods.find { |method| method.name == 'create' }
stock = invoke(create, nil, decomposer, 'jdk.certpath.disabledAlgorithms', nil)
permits_algorithm = disabled_class.declared_methods.find do |method|
  method.name == 'permits' && method.parameter_types.length == 3
end
primitives = EnumSet.of(BCCryptoPrimitive::SIGNATURE)
record.call('stock unconditional MD5 certificate signature restriction rejects MD5',
            !invoke(permits_algorithm, stock, primitives, 'MD5withRSA', nil))
record.call('conditional SHA1 expression is not interpreted by BC parser',
            invoke(permits_algorithm, stock, primitives, 'SHA1withRSA', nil))

Security.set_property('openvox.selfcheck.disabledAlgorithms', 'SHA1, RSA keySize < 2048')
explicit = invoke(create, nil, decomposer, 'openvox.selfcheck.disabledAlgorithms', nil)
record.call('supported unconditional SHA1 restriction rejects SHA1',
            !invoke(permits_algorithm, explicit, primitives, 'SHA1withRSA', nil))
permits_key = disabled_class.declared_methods.find do |method|
  method.name == 'permits' && method.parameter_types.length == 2
end
generator = java.security.KeyPairGenerator.get_instance('RSA', 'BC')
generator.java_send(:initialize, [Java::int], 1024)
record.call('supported keySize restriction rejects RSA 1024',
            !invoke(permits_key, explicit, primitives, generator.generate_key_pair.public))

factory = java.security.cert.CertificateFactory.get_instance('X.509', 'BC')
certificates = %w[root intermediate valid sha1].to_h do |name|
  input = java.io.ByteArrayInputStream.new(File.binread("#{ARGV.fetch(1)}/#{name}.pem").to_java_bytes)
  [name, factory.generate_certificate(input)]
end
constraints_class = java.lang.Class.for_name('org.bouncycastle.jsse.provider.ProvAlgorithmConstraints')
constructor = constraints_class.declared_constructors.find { |item| item.parameter_types.length == 3 }
constructor.accessible = true
restricted = constructor.new_instance(nil, ['ML-DSA-44'].to_java(:string), true)
default_field = constraints_class.get_declared_field('DEFAULT')
default_field.accessible = true
stock_constraints = default_field.get(nil)
checker = java.lang.Class.for_name('org.bouncycastle.jsse.provider.ProvAlgorithmChecker')
check_chain = checker.declared_methods.find { |method| method.name == 'checkChain' }
helper = Java::OrgBouncycastleJcajceUtil::BCJcaJceHelper.new
trusted = java.util.HashSet.new
trusted.add(certificates.fetch('root'))
purpose = Java::OrgBouncycastleAsn1X509::KeyPurposeId.java_class.get_field('id_kp_serverAuth').get(nil)
check = lambda do |constraints, chain|
  invoke(check_chain, nil, false.to_java(:boolean), helper, constraints, trusted,
         chain.to_java(java.security.cert.X509Certificate), purpose, 0.to_java(:int))
end
check.call(restricted, [certificates.fetch('valid'), certificates.fetch('root')])
record.call('deployment MLDSA certificate whitelist permits valid MLDSA chain', true)
mixed = %w[sha1 intermediate root].map { |name| certificates.fetch(name) }
check.call(stock_constraints, mixed)
record.call('stock BC chain checker accepts test private-root SHA1 chain', true)
begin
  check.call(restricted, mixed)
  raise 'Restricted certificate checker unexpectedly accepted SHA1 chain'
rescue Java::JavaLangReflect::InvocationTargetException => error
  record.call('deployment MLDSA certificate whitelist rejects valid RSA-SHA1-signed chain',
              error.target_exception.message.include?('not permitted'))
end
record.call('self-check did not replace maintained security restrictions',
            Security.get_property('jdk.tls.disabledAlgorithms') == stock_tls &&
            Security.get_property('jdk.certpath.disabledAlgorithms') == stock_certpath)
puts JSON.pretty_generate(passed: checks.length, checks: checks,
                          provider: Security.get_provider('BCJSSE').to_s,
                          scope: 'actual BC parser and certificate-chain checker; not a full TLS handshake')
