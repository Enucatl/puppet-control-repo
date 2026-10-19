require 'openssl'
require 'socket'
require 'timeout'
require 'net/http'
require 'json'
require 'rbconfig'

AGENT = 'rehearsal.home.arpa'
SERVER = 'openvox.rehearsal.test'
VAULT = 'vault.rehearsal.test'
KEY = "/agent/ssl/private_keys/#{AGENT}.pem"
CERT = "/agent/ssl/certs/#{AGENT}.pem"
ROOT = '/trust/root.pem'
CHAIN = '/trust/openvox-chain.pem'
CHECKS = {}
ERRORS = {}
OBSERVATIONS = {}
HANDSHAKES = []

def check(name)
  CHECKS[name] = !!yield
rescue StandardError => error
  CHECKS[name] = false
  # Network responses and exceptions may contain credentials.
  ERRORS[name] = error.class.name
end

def mldsa_signatures?(evidence)
  %w[client_signature server_signature].all? do |side|
    evidence[side].is_a?(String) && evidence[side].delete('-_').match?(/mldsa44/i)
  end
end

def handshake(host, port, hostname: host, trusted: true, classical: false, tls12: false, groups: nil)
  context = OpenSSL::SSL::SSLContext.new
  context.verify_mode = OpenSSL::SSL::VERIFY_PEER
  context.verify_hostname = true
  context.cert_store = OpenSSL::X509::Store.new
  context.cert_store.add_file(ROOT) if trusted
  context.cert = OpenSSL::X509::Certificate.new(File.read(CERT))
  context.key = OpenSSL::PKey.read(File.read(KEY))
  context.extra_chain_cert = File.read(CHAIN).scan(/-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----/m).map do |pem|
    OpenSSL::X509::Certificate.new(pem)
  end
  context.min_version = context.max_version = tls12 ? OpenSSL::SSL::TLS1_2_VERSION : OpenSSL::SSL::TLS1_3_VERSION
  context.options &= ~OpenSSL::SSL::OP_NO_TLSv1_2 if tls12
  context.ecdh_curves = groups || (classical ? 'X25519' : 'X25519MLKEM768')
  tcp = Socket.tcp(host, port, connect_timeout: 5)
  socket = OpenSSL::SSL::SSLSocket.new(tcp, context)
  socket.sync_close = true
  socket.hostname = hostname
  Timeout.timeout(5) { socket.connect }
  socket.post_connection_check(hostname)
  {
    'hostname' => hostname, 'port' => port, 'protocol' => socket.ssl_version,
    'cipher' => socket.cipher.first, 'group' => socket.group,
    'client_signature' => socket.sigalg, 'server_signature' => socket.peer_sigalg,
    'resumed' => socket.session_reused?
  }
ensure
  socket&.close
  tcp&.close
end

def rejected_tls?(pattern, ...)
  handshake(...)
  false
rescue OpenSSL::SSL::SSLError => error
  error.message.match?(pattern)
end

def vault_request(path, token: nil, body: nil)
  connection = Net::HTTP.new(VAULT, 8200, nil)
  connection.use_ssl = true
  connection.ca_file = ROOT
  connection.verify_mode = OpenSSL::SSL::VERIFY_PEER
  connection.verify_hostname = true
  connection.min_version = OpenSSL::SSL::TLS1_3_VERSION
  connection.cert = OpenSSL::X509::Certificate.new(File.read(CERT))
  connection.key = OpenSSL::PKey.read(File.read(KEY))
  connection.extra_chain_cert = File.read(CHAIN).scan(/-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----/m).map do |pem|
    OpenSSL::X509::Certificate.new(pem)
  end
  connection.open_timeout = 5
  connection.read_timeout = 20
  request = body ? Net::HTTP::Post.new("/v1/#{path}") : Net::HTTP::Get.new("/v1/#{path}")
  request['X-Vault-Token'] = token if token
  request['Content-Type'] = 'application/json'
  request.body = JSON.generate(body) if body
  response = connection.start { |http| http.request(request) }
  [response.code.to_i, JSON.parse(response.body)]
end

check('agent_seed_only_key_and_mldsa44_certificate') do
  system(RbConfig.ruby, '/rehearsal/verify-crypto.rb', '--key', KEY, '--cert', CERT,
         out: File::NULL, err: File::NULL)
end
check('agent_key_matches_verified_leaf') do
  leaf = OpenSSL::X509::Certificate.new(File.read(CERT))
  key = OpenSSL::PKey.read(File.read(KEY))
  issuer = OpenSSL::X509::Certificate.new(File.read(CHAIN))
  key.public_to_der == leaf.public_key.public_to_der && leaf.verify(issuer.public_key)
end

check('actual_native_agent_mtls13_hybrid_mldsa44') do
  traces = File.readlines(ENV.fetch('MLDSA_TLS_EVIDENCE', '/agent/tls.jsonl')).map { |line| JSON.parse(line) }
  native = traces.select { |tls| tls['program'] == 'puppet' && tls['port'] == 8140 && tls['client_certificate'] }
  HANDSHAKES.concat(native)
  !native.empty? && native.all? do |tls|
    tls['hostname'] == SERVER && tls['protocol'] == 'TLSv1.3' &&
      tls['group'] == 'X25519MLKEM768' && tls['resumed'] == false && mldsa_signatures?(tls)
  end
end

[[SERVER, 8140], [VAULT, 8200]].each do |host, port|
  check("#{host}_full_mtls13_hybrid_mldsa44") do
    tls = handshake(host, port)
    HANDSHAKES << tls
    tls['protocol'] == 'TLSv1.3' && tls['group'] == 'X25519MLKEM768' &&
      tls['resumed'] == false && mldsa_signatures?(tls)
  end
  check("#{host}_wrong_hostname_denied") do
    rejected_tls?(/hostname mismatch|certificate verify failed/i, host, port, hostname: 'wrong.rehearsal.test')
  end
  check("#{host}_wrong_ca_denied") do
    rejected_tls?(/certificate verify failed/i, host, port, trusted: false)
  end
end
check('server_tls12_denied') do
  rejected_tls?(/alert protocol version|unsupported protocol|wrong version number/i, SERVER, 8140, tls12: true)
end
check('server_classical_key_exchange_denied') do
  rejected_tls?(/handshake failure|no suitable key share|no shared groups/i, SERVER, 8140, classical: true)
end
check('vault_prefers_hybrid_key_exchange') do
  tls = handshake(VAULT, 8200, groups: 'X25519MLKEM768:X25519')
  HANDSHAKES << tls
  tls['protocol'] == 'TLSv1.3' && tls['group'] == 'X25519MLKEM768' && mldsa_signatures?(tls)
end
check('vault_classical_fallback_recorded') do
  begin
    tls = handshake(VAULT, 8200, classical: true)
    HANDSHAKES << tls
    OBSERVATIONS['vault_classical_fallback'] = { accepted: true, policy: 'temporary operator-approved exception' }
    tls['protocol'] == 'TLSv1.3' && tls['group'].casecmp?('X25519') && mldsa_signatures?(tls)
  rescue OpenSSL::SSL::SSLError => error
    OBSERVATIONS['vault_classical_fallback'] = { accepted: false, policy: 'temporary operator-approved exception' }
    error.message.match?(/handshake failure|no suitable key share|no shared groups/i)
  end
end

token = nil
check('vault_agent_certificate_login') do
  status, response = vault_request('auth/cert/login', body: { name: 'puppet' })
  token = response.dig('auth', 'client_token')
  status == 200 && token.is_a?(String) && !token.empty?
end
check('vault_agent_kv_read_denied') do
  token && vault_request('kv/data/puppet', token: token).first == 403
end
check('vault_agent_bound_service_issuance') do
  raise 'No enrolled Vault identity' unless token

  status, response = vault_request('pki_int/issue/puppet', token: token, body: { common_name: "service.#{AGENT}", ttl: '1h' })
  raise 'Service issuance denied' unless status == 200

  data = response.fetch('data')
  File.write('/agent/service-key.pem', data.fetch('private_key'), mode: 'w', perm: 0o600)
  File.write('/agent/service-cert.pem', data.fetch('certificate'), mode: 'w', perm: 0o600)
  cert = OpenSSL::X509::Certificate.new(data.fetch('certificate'))
  key = OpenSSL::PKey.read(data.fetch('private_key'))
  store = OpenSSL::X509::Store.new
  store.add_file(ROOT)
  chain = data.fetch('ca_chain').map { |pem| OpenSSL::X509::Certificate.new(pem) }
  cert.subject.to_a.select { |name, _value, _type| name == 'CN' }.map { |_name, value, _type| value } == ["service.#{AGENT}"] &&
    store.verify(cert, chain) && key.public_to_der == cert.public_key.public_to_der &&
    system(RbConfig.ruby, '/rehearsal/verify-crypto.rb', '--key', '/agent/service-key.pem', '--cert', '/agent/service-cert.pem', out: File::NULL, err: File::NULL)
end
{
  'vault_other_common_name_denied' => { common_name: 'other.home.arpa' },
  'vault_other_dns_san_denied' => { common_name: "service.#{AGENT}", alt_names: 'other.home.arpa' },
  'vault_ip_san_denied' => { common_name: "service.#{AGENT}", ip_sans: '127.0.0.1' }
}.each do |name, request|
  check(name) { token && [400, 403].include?(vault_request('pki_int/issue/puppet', token: token, body: request).first) }
end
check('vault_other_role_denied') do
  token && vault_request('pki_int/issue/other-role', token: token, body: { common_name: "service.#{AGENT}" }).first == 403
end

puts JSON.generate(checks: CHECKS, errors: ERRORS, observations: OBSERVATIONS, handshakes: HANDSHAKES)
exit(CHECKS.values.all? ? 0 : 1)
