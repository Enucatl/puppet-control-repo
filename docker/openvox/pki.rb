require 'openssl'
require 'fileutils'

File.umask(0o077)
base = '/opt/puppetlabs/server/data/puppetserver/server_data/bootstrap'
FileUtils.mkdir_p(base)
if ARGV.fetch(0) == 'prepare'
  key_path = "#{base}/intermediate.key"
  unless File.exist?(key_path)
    key = OpenSSL::PKey.generate_key('ML-DSA-44')
    request = OpenSSL::X509::Request.new
    request.version = 0
    request.subject = OpenSSL::X509::Name.parse('/CN=OpenVox rehearsal intermediate')
    request.public_key = key
    request.sign(key, nil)
    File.write(key_path, key.private_to_pem)
    File.write("#{base}/intermediate.csr", request.to_pem)
  end
  ssl = '/etc/puppetlabs/puppet/ssl'
  FileUtils.mkdir_p(["#{ssl}/private_keys", "#{ssl}/public_keys"])
  host_key = "#{ssl}/private_keys/openvox.rehearsal.test.pem"
  unless File.exist?(host_key)
    key = OpenSSL::PKey.generate_key('ML-DSA-44')
    File.write(host_key, key.private_to_pem)
    File.write("#{ssl}/public_keys/openvox.rehearsal.test.pem", key.public_to_pem)
  end
  puts File.read("#{base}/intermediate.csr")
elsif ARGV.fetch(0) == 'check-issuer'
  issuer = OpenSSL::X509::Certificate.new(File.read("#{base}/import.pem"))
  abort 'Imported issuer must outlive the configured 24-hour server leaf' unless issuer.not_after > Time.now + 86400
elsif ARGV.fetch(0) == 'chain'
  path = '/etc/puppetlabs/puppet/ssl/certs/openvox.rehearsal.test.pem'
  leaf = OpenSSL::X509::Certificate.new(File.read(path))
  File.write(path, leaf.to_pem + File.read('/trust/openvox-chain.pem'))
else
  abort 'Unknown PKI operation'
end
