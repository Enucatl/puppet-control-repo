require 'openssl'
require 'fileutils'

# Explicit bootstrap transport identity, pinned locally before any Vault request.
abort 'Refusing to replace existing Vault state' if File.exist?('/state/tls.key') || File.exist?('/state/data')
File.umask(0o077)
key = OpenSSL::PKey.generate_key('ML-DSA-44')
name = ARGV.fetch(0)
cert = OpenSSL::X509::Certificate.new
cert.version = 2
cert.serial = OpenSSL::BN.rand(128).to_i
cert.subject = cert.issuer = OpenSSL::X509::Name.parse("/CN=#{name}")
cert.public_key = key
cert.not_before = Time.now - 60
cert.not_after = Time.now + 86400
extensions = OpenSSL::X509::ExtensionFactory.new
extensions.subject_certificate = extensions.issuer_certificate = cert
cert.add_extension(extensions.create_extension('basicConstraints', 'CA:FALSE', true))
cert.add_extension(extensions.create_extension('keyUsage', 'digitalSignature', true))
cert.add_extension(extensions.create_extension('extendedKeyUsage', 'serverAuth'))
cert.add_extension(extensions.create_extension('subjectAltName', "DNS:#{name},DNS:localhost"))
cert.sign(key, nil)
File.write('/state/tls.key', key.private_to_pem)
File.write('/state/tls.crt', cert.to_pem)
File.write('/state/trust.pem', cert.to_pem)
