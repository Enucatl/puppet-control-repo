# Explicit offline-root path; the legacy setup image cannot run this helper.
require 'openssl'
require 'net/http'
require 'json'
require 'digest'
require 'fileutils'

def fail_closed(message)
  abort "Offline intermediate: #{message}"
end

def api(method, path, body = nil, accepted: [200, 204])
  uri = URI(ENV.fetch('VAULT_ADDR'))
  http = Net::HTTP.new(uri.host, uri.port, nil)
  http.use_ssl = true
  http.verify_mode = OpenSSL::SSL::VERIFY_PEER
  http.ca_file = ENV.fetch('VAULT_CACERT')
  http.min_version = http.max_version = OpenSSL::SSL::TLS1_3_VERSION
  http.open_timeout = http.read_timeout = 30
  request = Net::HTTPGenericRequest.new(method, !body.nil?, true, "/v1/#{path}")
  request['X-Vault-Token'] = ENV.fetch('VAULT_TOKEN')
  request['Content-Type'] = 'application/json'
  request.body = JSON.generate(body) if body
  response = http.request(request)
  fail_closed("#{method} #{path} returned HTTP #{response.code}") unless accepted.include?(response.code.to_i)
  response.body.to_s.empty? ? {} : JSON.parse(response.body)
end

def mldsa?(certificate)
  certificate.signature_algorithm == 'ML-DSA-44' &&
    OpenSSL::ASN1.decode(certificate.public_key.public_to_der).value[0].value[0].oid == '2.16.840.1.101.3.4.3.17'
end

def validate_chain(directory, fingerprint)
  root = OpenSSL::X509::Certificate.new(File.read("#{directory}/root.pem"))
  fail_closed('root certificate does not match operator pin') unless
    fingerprint.match?(/\A[0-9a-f]{64}\z/) && Digest::SHA256.hexdigest(root.to_der) == fingerprint
  chain_pem = File.read("#{directory}/signed.pem")
  chain = chain_pem.scan(/-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----/m).map { |pem| OpenSSL::X509::Certificate.new(pem) }
  fail_closed('expected intermediate followed by pinned root') unless chain.length == 2 && chain.last.to_der == root.to_der
  intermediate = chain.first
  csr = OpenSSL::X509::Request.new(File.read("#{directory}/intermediate.csr"))
  fail_closed('CSR signature or intermediate key mismatch') unless
    csr.verify(csr.public_key) && csr.public_key.public_to_der == intermediate.public_key.public_to_der &&
    csr.subject.to_der == intermediate.subject.to_der
  fail_closed('invalid root/intermediate signature or algorithm') unless
    [root, intermediate].all? { |cert| mldsa?(cert) } &&
    root.subject.to_der == root.issuer.to_der && root.verify(root.public_key) &&
    intermediate.issuer.to_der == root.subject.to_der && intermediate.verify(root.public_key)
  now = Time.now
  fail_closed('expired, short-lived or unbounded issuer chain') unless
    [root, intermediate].all? { |cert| cert.not_before <= now && now < cert.not_after } &&
    intermediate.not_after > now + 86400 && intermediate.not_after <= root.not_after
  [[root, 1], [intermediate, 0]].each do |cert, length|
    constraints = cert.extensions.find { |extension| extension.oid == 'basicConstraints' }
    usage = cert.extensions.find { |extension| extension.oid == 'keyUsage' }
    fail_closed('unexpected CA path length or key usage') unless
      constraints && constraints.critical? && constraints.value == "CA:TRUE, pathlen:#{length}" &&
      usage && usage.value.include?('Certificate Sign') && usage.value.include?('CRL Sign')
  end
  [intermediate, chain_pem]
end

if $PROGRAM_NAME == __FILE__
  action = ARGV.shift
  initialize_mount = ARGV.delete('--initialize')
  directory = ARGV.shift
  fingerprint = ARGV.shift
  fail_closed('usage: generate-csr --initialize DIRECTORY | import DIRECTORY ROOT_SHA256') unless
    ARGV.empty? && directory && ((action == 'generate-csr' && initialize_mount && !fingerprint) ||
                               (action == 'import' && !initialize_mount && fingerprint))
  uri = URI(ENV.fetch('VAULT_ADDR', ''))
  fail_closed('explicit verified HTTPS VAULT_ADDR is required') unless
    uri.scheme == 'https' && uri.host && !uri.userinfo && ['', '/'].include?(uri.path) && !uri.query && !uri.fragment
  fail_closed('explicit administrator VAULT_TOKEN is required') if ENV.fetch('VAULT_TOKEN', '').empty?
  %w[VAULT_CACERT OPENSSL_CONF].each do |name|
    fail_closed("readable explicit #{name} is required") unless File.file?(ENV.fetch(name, '')) && File.readable?(ENV[name])
  end
  fail_closed('VAULT_SKIP_VERIFY is forbidden') unless ['', 'false', '0'].include?(ENV.fetch('VAULT_SKIP_VERIFY', '').downcase)
  fail_closed('VAULT_TLS_SERVER_NAME override is forbidden') unless ENV.fetch('VAULT_TLS_SERVER_NAME', '').empty?
  File.umask(0o077)
  mounts = api('GET', 'sys/mounts').fetch('data')
  unless mounts.key?('pki_int/')
    fail_closed('missing established pki_int; restore state or explicitly generate-csr --initialize') unless initialize_mount
    api('POST', 'sys/mounts/pki_int', {'type' => 'pki', 'config' => {'max_lease_ttl' => '720h'}})
  end
  fail_closed('pki_int is not a PKI mount') if mounts.key?('pki_int/') && mounts.fetch('pki_int/').fetch('type') != 'pki'
  issuers = api('LIST', 'pki_int/issuers', accepted: [200, 404]).dig('data', 'keys') || []
  current = issuers.empty? ? nil : api('GET', 'pki_int/issuer/default').fetch('data')
  if action == 'generate-csr'
    fail_closed('existing issuer must be restored/renewed, never regenerated') if current
    keys = api('LIST', 'pki_int/keys', accepted: [200, 404]).dig('data', 'keys') || []
    fail_closed('existing key or CSR state; recover the original request instead of generating a replacement') unless
      keys.empty? && !File.exist?(directory)
    FileUtils.mkdir_p(directory)
    generated = api('POST', 'pki_int/intermediate/generate/internal',
                    {'common_name' => 'FreeIPA ML-DSA intermediate', 'key_type' => 'ml-dsa', 'parameter_set' => 'ml-dsa-44'}).fetch('data')
    File.write("#{directory}/intermediate.csr", generated.fetch('csr'))
    File.write("#{directory}/request.json", JSON.generate({'key_id' => generated.fetch('key_id'), 'csr_sha256' => Digest::SHA256.hexdigest(generated.fetch('csr'))}))
    puts "CSR prepared at #{directory}/intermediate.csr; have the offline root sign it with path length zero."
  else
    state = JSON.parse(File.read("#{directory}/request.json"))
    fail_closed('original CSR state changed') unless Digest::SHA256.hexdigest(File.read("#{directory}/intermediate.csr")) == state.fetch('csr_sha256')
    intermediate, chain = validate_chain(directory, fingerprint)
    if current
      fail_closed('existing issuer differs; restore/renew it explicitly') unless
        OpenSSL::X509::Certificate.new(current.fetch('certificate')).to_der == intermediate.to_der && current.fetch('key_id') == state.fetch('key_id')
      puts 'Existing intermediate matches verified import; no state changed.'
    else
      keys = api('LIST', 'pki_int/keys').dig('data', 'keys')
      fail_closed('original Vault key is missing; restore protected state') unless keys.include?(state.fetch('key_id'))
      api('POST', 'pki_int/intermediate/set-signed', {'certificate' => chain})
      installed = api('GET', 'pki_int/issuer/default').fetch('data')
      fail_closed('imported issuer/key mismatch') unless
        OpenSSL::X509::Certificate.new(installed.fetch('certificate')).to_der == intermediate.to_der && installed.fetch('key_id') == state.fetch('key_id')
      puts 'Imported separate offline-root-signed FreeIPA ML-DSA intermediate; configure publication URLs and scoped leaf roles before use.'
    end
  end
end
