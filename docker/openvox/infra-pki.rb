#!/opt/puppetlabs/puppet/bin/ruby

require 'digest'
require 'fileutils'
require 'openssl'
require 'rbconfig'

File.umask(0o077)
bootstrap = '/opt/puppetlabs/server/data/puppetserver/server_data/bootstrap'
ssl = '/etc/puppetlabs/puppet/ssl'
ca = '/etc/puppetlabs/puppetserver/ca'
host = 'openvox.home.arpa'
abort 'Existing authority must be restored/renewed, never replaced by fresh import' if
  %w[ca_key.pem ca_crt.pem].any? { |name| File.exist?("#{ca}/#{name}") }
abort 'Existing server certificate must be restored/renewed, never replaced by fresh import' if
  File.exist?("#{ssl}/certs/#{host}.pem")

verify = lambda do |*arguments|
  abort 'ML-DSA-44 seed-only key/certificate validation failed' unless
    system(RbConfig.ruby, File.join(__dir__, 'verify-crypto.rb'), *arguments,
           out: File::NULL, err: File::NULL)
end

case ARGV.shift
when 'prepare'
  abort 'Usage: infra-pki.rb prepare' unless ARGV.empty?
  FileUtils.mkdir_p([bootstrap, "#{ssl}/private_keys", "#{ssl}/public_keys"])
  ["#{bootstrap}/intermediate.key", "#{ssl}/private_keys/#{host}.pem"].each do |path|
    unless File.exist?(path)
      File.write(path, OpenSSL::PKey.generate_key('ML-DSA-44').private_to_pem,
                 mode: 'wx', perm: 0o600)
    end
    verify.call('--key', path)
  end
  key = OpenSSL::PKey.read(File.read("#{bootstrap}/intermediate.key"))
  request = OpenSSL::X509::Request.new
  request.version = 0
  request.subject = OpenSSL::X509::Name.parse('/CN=OpenVox intermediate')
  request.public_key = key
  request.sign(key, nil)
  File.write("#{bootstrap}/intermediate.csr", request.to_pem)
  host_key = OpenSSL::PKey.read(File.read("#{ssl}/private_keys/#{host}.pem"))
  public_path = "#{ssl}/public_keys/#{host}.pem"
  abort 'Existing host public/private keys do not match' if File.exist?(public_path) &&
    OpenSSL::PKey.read(File.read(public_path)).public_to_der != host_key.public_to_der
  File.write(public_path, host_key.public_to_pem)
  puts request.to_pem
when 'import'
  option, fingerprint = ARGV
  abort 'Usage: infra-pki.rb import --root-sha256 ROOT_CERT_DER_SHA256' unless
    ARGV.length == 2 && option == '--root-sha256' && fingerprint.match?(/\A[0-9a-fA-F]{64}\z/)
  roots = File.read('/trust/root.pem').scan(/-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----/m)
  abort 'Expected exactly one pinned offline root certificate' unless roots.length == 1
  root = OpenSSL::X509::Certificate.new(roots.first)
  abort 'Offline root fingerprint does not match operator pin' unless
    Digest::SHA256.hexdigest(root.to_der) == fingerprint.downcase
  chain = File.read('/trust/openvox-chain.pem').scan(/-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----/m).map do |pem|
    OpenSSL::X509::Certificate.new(pem)
  end
  intermediate, chain_root = chain
  abort 'Expected intermediate followed by pinned offline root' unless
    chain.length == 2 && chain_root.to_der == root.to_der
  key_path = "#{bootstrap}/intermediate.key"
  host_key_path = "#{ssl}/private_keys/#{host}.pem"
  verify.call('--key', key_path, '--key', host_key_path,
              '--cert', '/trust/root.pem', '--cert', '/trust/openvox-chain.pem',
              '--crl', '/trust/root.crl')
  key = OpenSSL::PKey.read(File.read(key_path))
  host_key = OpenSSL::PKey.read(File.read(host_key_path))
  now = Time.now
  abort 'Invalid chain, intermediate key pair, or issuer lifetime' unless
    root.subject.to_der == root.issuer.to_der && root.verify(root.public_key) &&
    intermediate.issuer.to_der == root.subject.to_der && intermediate.verify(root.public_key) &&
    intermediate.public_key.public_to_der == key.public_to_der &&
    key.public_to_der != root.public_key.public_to_der &&
    host_key.public_to_der != root.public_key.public_to_der && host_key.public_to_der != key.public_to_der &&
    [root, intermediate].all? { |cert| cert.not_before <= now && now < cert.not_after } &&
    intermediate.not_after > now + 86400 && intermediate.not_after <= root.not_after
  [[root, 1], [intermediate, 0]].each do |cert, path_length|
    constraints = cert.extensions.find { |ext| ext.oid == 'basicConstraints' }
    usage = cert.extensions.find { |ext| ext.oid == 'keyUsage' }
    abort 'Invalid authority constraints' unless
      constraints&.critical? && constraints.value == "CA:TRUE, pathlen:#{path_length}" &&
      usage&.critical? && usage.value.include?('Certificate Sign') && usage.value.include?('CRL Sign')
  end
  root_crl = File.read('/trust/root.crl')
  crl = OpenSSL::X509::CRL.new
  crl.version = 1
  crl.issuer = intermediate.subject
  crl.last_update = now - 60
  crl.next_update = now + 5 * 365 * 86400
  crl.add_extension(OpenSSL::X509::Extension.new('crlNumber', OpenSSL::ASN1::Integer(0).to_der))
  crl.sign(key, nil)
  File.write("#{bootstrap}/import.pem", chain.map(&:to_pem).join)
  File.write("#{bootstrap}/crls.pem", crl.to_pem + root_crl)
  abort 'Packaged CA import failed; inspect state before retrying' unless system(
    '/opt/puppetlabs/bin/puppetserver', 'ca', 'import',
    '--config', '/etc/puppetlabs/puppet/puppet.conf',
    '--private-key', key_path, '--cert-bundle', "#{bootstrap}/import.pem",
    '--crl-chain', "#{bootstrap}/crls.pem", '--subject-alt-names', host
  )
  path = "#{ssl}/certs/#{host}.pem"
  leaf = OpenSSL::X509::Certificate.new(File.read(path))
  abort 'Imported server identity or lifetime is invalid; do not start the server' unless
    leaf.verify(intermediate.public_key) && leaf.not_after <= intermediate.not_after &&
    leaf.public_key.public_to_der == host_key.public_to_der &&
    OpenSSL::SSL.verify_certificate_identity(leaf, host)
  File.write(path, leaf.to_pem + chain.map(&:to_pem).join)
  verify.call('--key', "#{ca}/ca_key.pem", '--cert', path, '--crl', "#{ca}/ca_crl.pem")
  puts 'Imported pinned offline-root-signed OpenVox intermediate and matching server identity'
else
  abort 'Usage: infra-pki.rb prepare | import --root-sha256 ROOT_CERT_DER_SHA256'
end
