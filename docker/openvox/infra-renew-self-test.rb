#!/opt/puppetlabs/puppet/bin/ruby

require 'digest'
require 'fileutils'
require 'json'
require 'openssl'
require 'tmpdir'
require_relative 'infra-renew'

def assert(condition, message)
  raise message unless condition
end

def certificate(key, serial:, name:, issuer: nil, signer: key, days:, ca: nil)
  cert = OpenSSL::X509::Certificate.new
  cert.version = 2
  cert.serial = serial
  cert.subject = OpenSSL::X509::Name.parse("/CN=#{name}")
  cert.issuer = issuer ? issuer.subject : cert.subject
  cert.public_key = key
  cert.not_before = Time.now - 60
  cert.not_after = Time.now + days * 86400
  factory = OpenSSL::X509::ExtensionFactory.new
  factory.subject_certificate = cert
  factory.issuer_certificate = issuer || cert
  cert.add_extension(factory.create_extension('basicConstraints', ca ? "CA:TRUE,pathlen:#{ca}" : 'CA:FALSE', true))
  cert.add_extension(factory.create_extension('keyUsage', ca ? 'keyCertSign,cRLSign' : 'digitalSignature', true))
  cert.add_extension(factory.create_extension('subjectKeyIdentifier', 'hash'))
  cert.add_extension(factory.create_extension('authorityKeyIdentifier', 'keyid'))
  unless ca
    cert.add_extension(factory.create_extension('extendedKeyUsage', 'serverAuth,clientAuth'))
    cert.add_extension(factory.create_extension('subjectAltName', "DNS:#{name}"))
  end
  cert.sign(signer, nil)
  cert
end

def fixture(directory, issuer_days: 365 * 5, leaf_days: 1)
  %i[SSL CA TRUST STATE].each do |constant|
    OpenVoxRenewal.send(:remove_const, constant)
    OpenVoxRenewal.const_set(constant, File.join(directory, constant.to_s.downcase))
  end
  [OpenVoxRenewal::SSL, OpenVoxRenewal::CA, OpenVoxRenewal::TRUST, OpenVoxRenewal::STATE,
   "#{OpenVoxRenewal::SSL}/certs", "#{OpenVoxRenewal::SSL}/private_keys"].each { |path| FileUtils.mkdir_p(path) }
  root_key, ca_key, key = Array.new(3) { OpenSSL::PKey.generate_key('ML-DSA-44') }
  root = certificate(root_key, serial: 1, name: 'Test root', days: 365 * 10, ca: 1)
  intermediate = certificate(ca_key, serial: 2, name: 'Test intermediate', issuer: root,
                             signer: root_key, days: issuer_days, ca: 0)
  leaf = certificate(key, serial: 3, name: OpenVoxRenewal::HOST, issuer: intermediate,
                     signer: ca_key, days: leaf_days)
  chain = [intermediate, root]
  live = "#{OpenVoxRenewal::SSL}/certs/#{OpenVoxRenewal::HOST}.pem"
  files = {
    live => leaf.to_pem + chain.map(&:to_pem).join,
    "#{OpenVoxRenewal::SSL}/private_keys/#{OpenVoxRenewal::HOST}.pem" => key.private_to_pem,
    "#{OpenVoxRenewal::SSL}/certs/ca.pem" => chain.map(&:to_pem).join,
    "#{OpenVoxRenewal::CA}/ca_key.pem" => ca_key.private_to_pem,
    "#{OpenVoxRenewal::CA}/ca_crt.pem" => chain.map(&:to_pem).join,
    "#{OpenVoxRenewal::TRUST}/root.pem" => root.to_pem,
    "#{OpenVoxRenewal::TRUST}/openvox-chain.pem" => chain.map(&:to_pem).join
  }
  files.each { |path, contents| File.write(path, contents) }
  {key: key, signer: ca_key, chain: chain, leaf: leaf, live: live, files: files,
   marker: "#{OpenVoxRenewal::STATE}/openvox-reload.pending"}
end

def renewed(state, key: state[:key], name: OpenVoxRenewal::HOST, days: 60)
  certificate(key, serial: 4, name: name, issuer: state[:chain].first,
              signer: state[:signer], days: days)
end

def expect_failure
  begin
    yield
  rescue StandardError
    return
  end
  raise 'Expected renewal refusal'
end

def unchanged_authority(state)
  state[:files].each do |path, contents|
    next if path == state[:live]
    assert(File.read(path) == contents, "Authority or key changed: #{path}")
  end
end

def with_listener(state, leaf)
  context = OpenSSL::SSL::SSLContext.new
  context.min_version = context.max_version = OpenSSL::SSL::TLS1_3_VERSION
  context.cert = leaf
  context.key = state[:key]
  context.extra_chain_cert = state[:chain]
  listener = TCPServer.new('127.0.0.1', 8140)
  server = OpenSSL::SSL::SSLServer.new(listener, context)
  thread = Thread.new do
    connection = server.accept
    connection.close
  end
  yield
ensure
  listener&.close
  if thread
    unless thread.join(5)
      thread.kill
      raise 'Test TLS listener did not finish'
    end
    thread.value
  end
end

checks = []
Dir.mktmpdir('openvox-renewal-test-') do |directory|
  state = fixture(File.join(directory, 'success'))
  calls = []
  OpenVoxRenewal.define_singleton_method(:run_builtin) do |candidate|
    calls << candidate
    File.write(candidate, renewed(state).to_pem)
  end
  result = OpenVoxRenewal.renew
  assert(result[:changed] && result[:reload_required], 'Successful renewal must request reload')
  served = OpenVoxRenewal.certificates(File.read(state[:live]))
  assert(served.length == 3 && served.first.serial == 4, 'Renewal must publish a full new chain')
  assert(served.drop(1).map(&:to_der) == state[:chain].map(&:to_der), 'Issuer chain changed')
  assert((served.first.not_after - Time.now).between?(59 * 86400, 60 * 86400), 'Expected sixty-day leaf')
  assert(File.exist?(state[:marker]), 'Reload marker missing')
  unchanged_authority(state)
  result = OpenVoxRenewal.renew
  assert(!result[:changed] && result[:reload_required], 'Repeat must retain pending reload without reissuing')
  assert(calls.length == 1 && File.exist?(state[:marker]), 'Repeat lost pending state or issued again')
  checks << 'same-key sixty-day renewal preserves full chain and pending reload across repeat'

  state = fixture(File.join(directory, 'not-due'), leaf_days: 59)
  OpenVoxRenewal.define_singleton_method(:run_builtin) { |_candidate| raise 'Unnecessary renewal request' }
  result = OpenVoxRenewal.renew
  assert(!result[:changed] && !result[:reload_required], 'Not-due certificate should remain unchanged')
  assert(File.read(state[:live]) == state[:files][state[:live]], 'Not-due live chain changed')
  unchanged_authority(state)
  checks << 'certificate outside renewal window needs neither issuance nor restart'

  state = fixture(File.join(directory, 'api-failure'))
  OpenVoxRenewal.define_singleton_method(:run_builtin) { |_candidate| raise 'Simulated API failure' }
  expect_failure { OpenVoxRenewal.renew }
  assert(File.read(state[:live]) == state[:files][state[:live]], 'Failed API changed live chain')
  assert(!File.exist?(state[:marker]), 'Failed API scheduled a reload')
  unchanged_authority(state)
  checks << 'failed API preserves live certificate and authority'

  %w[key san rsa short long].each do |mode|
    state = fixture(File.join(directory, mode))
    candidate = case mode
                when 'key' then renewed(state, key: OpenSSL::PKey.generate_key('ML-DSA-44'))
                when 'san'
                  leaf = renewed(state)
                  factory = OpenSSL::X509::ExtensionFactory.new
                  leaf.extensions = leaf.extensions.reject { |extension| extension.oid == 'subjectAltName' } +
                                    [factory.create_extension('subjectAltName', 'DNS:wrong.home.arpa')]
                  leaf.sign(state[:signer], nil)
                  leaf
                when 'rsa' then renewed(state, key: OpenSSL::PKey.generate_key('RSA', 'rsa_keygen_bits' => 2048))
                when 'short' then renewed(state, days: 1)
                when 'long' then renewed(state, days: 61)
                end
    OpenVoxRenewal.define_singleton_method(:run_builtin) { |path| File.write(path, candidate.to_pem) }
    expect_failure { OpenVoxRenewal.renew }
    assert(File.read(state[:live]) == state[:files][state[:live]], "Rejected #{mode} changed live chain")
    assert(!File.exist?(state[:marker]), "Rejected #{mode} scheduled reload")
    unchanged_authority(state)
    checks << "rejects #{mode} candidate before publishing"
  end

  state = fixture(File.join(directory, 'short-issuer'), issuer_days: 59)
  calls = []
  OpenVoxRenewal.define_singleton_method(:run_builtin) { |path| calls << path }
  expect_failure { OpenVoxRenewal.renew }
  assert(calls.empty?, 'Short issuer must be refused before API renewal')
  assert(File.read(state[:live]) == state[:files][state[:live]], 'Short issuer changed live chain')
  checks << 'issuer lifetime preflight prevents unusable issuance'

  state = fixture(File.join(directory, 'resume'))
  calls = []
  OpenVoxRenewal.define_singleton_method(:run_builtin) do |candidate|
    calls << candidate
    File.write(candidate, renewed(state).to_pem)
    raise 'Simulated interruption after persisting the renewal response'
  end
  expect_failure { OpenVoxRenewal.renew }
  assert(File.read(state[:live]) == state[:files][state[:live]], 'Interrupted response changed live chain')
  saved_candidate = File.read(calls.first)
  OpenVoxRenewal.define_singleton_method(:run_builtin) do |candidate|
    calls << candidate
    assert(File.read(candidate) == saved_candidate, 'Saved candidate was overwritten before validation')
  end
  result = OpenVoxRenewal.renew
  assert(result[:changed] && result[:reload_required] && calls.length == 2, 'Saved candidate was not revalidated')
  assert(OpenVoxRenewal.certificates(File.read(state[:live])).first.to_pem == saved_candidate,
         'Resume must publish the saved serial without issuing again')
  unchanged_authority(state)
  checks << 'persisted valid response is revalidated and resumes without a second issuance'

  state = fixture(File.join(directory, 'revoked-saved-candidate'))
  File.write("#{OpenVoxRenewal::STATE}/openvox-renewal.pem", renewed(state).to_pem)
  calls = []
  OpenVoxRenewal.define_singleton_method(:run_builtin) do |path|
    calls << path
    raise 'Simulated builtin CRL validation failure'
  end
  expect_failure { OpenVoxRenewal.renew }
  assert(calls.length == 1, 'Saved renewal must pass builtin validation before publication')
  assert(File.read(state[:live]) == state[:files][state[:live]], 'Rejected saved renewal changed live chain')
  assert(!File.exist?(state[:marker]), 'Rejected saved renewal scheduled reload')
  checks << 'failed builtin validation of saved candidate prevents publication'

  state = fixture(File.join(directory, 'invalid-saved-candidate'))
  File.write("#{OpenVoxRenewal::STATE}/openvox-renewal.pem", renewed(state, days: 61).to_pem)
  calls = []
  OpenVoxRenewal.define_singleton_method(:run_builtin) { |path| calls << path }
  expect_failure { OpenVoxRenewal.renew }
  assert(calls.length == 1, 'Saved candidate must pass through builtin validation')
  assert(File.read(state[:live]) == state[:files][state[:live]], 'Invalid saved candidate changed live chain')
  assert(!File.exist?(state[:marker]), 'Invalid saved candidate scheduled reload')
  checks << 'saved candidate obeys the same sixty-day lifetime ceiling'

  state = fixture(File.join(directory, 'publish-interruption'))
  OpenVoxRenewal.define_singleton_method(:run_builtin) { |path| File.write(path, renewed(state).to_pem) }
  original_write = OpenVoxRenewal.method(:atomic_write)
  OpenVoxRenewal.define_singleton_method(:atomic_write) do |path, contents|
    if path.to_s == state[:live]
      assert(File.exist?(state[:marker]), 'Reload marker must precede live publication')
      raise 'Simulated interruption before atomic certificate replacement'
    end
    original_write.call(path, contents)
  end
  expect_failure { OpenVoxRenewal.renew }
  assert(File.exist?(state[:marker]), 'Interrupted publication lost pending reload')
  assert(File.read(state[:live]) == state[:files][state[:live]], 'Interrupted publication damaged live chain')
  OpenVoxRenewal.define_singleton_method(:atomic_write, original_write)
  saved_candidate = File.read("#{OpenVoxRenewal::STATE}/openvox-renewal.pem")
  validations = []
  OpenVoxRenewal.define_singleton_method(:run_builtin) do |candidate|
    validations << candidate
    assert(File.read(candidate) == saved_candidate, 'Interrupted publication must reuse its staged serial')
  end
  assert(OpenVoxRenewal.renew[:changed], 'Interrupted publication did not recover')
  assert(validations.length == 1, 'Interrupted publication must revalidate its staged certificate')
  assert(OpenVoxRenewal.certificates(File.read(state[:live])).first.to_pem == saved_candidate,
         'Interrupted publication changed the staged identity')
  unchanged_authority(state)
  checks << 'reload marker precedes atomic publication and interrupted publication resumes'

  with_listener(state, state[:leaf]) { expect_failure { OpenVoxRenewal.verify } }
  assert(File.exist?(state[:marker]), 'Old listener must not acknowledge the pending renewal')
  checks << 'trusted old listener certificate fails verification and preserves pending reload'
  current = OpenVoxRenewal.certificates(File.read(state[:live])).first
  with_listener(state, current) do
    result = OpenVoxRenewal.verify
    assert(result[:listener_verified], 'Matching listener certificate was not verified')
    assert(result[:leaf_sha256] == Digest::SHA256.hexdigest(current.to_der), 'Wrong listener identity recorded')
  end
  assert(!File.exist?(state[:marker]), 'Verified listener must clear pending reload')
  result = OpenVoxRenewal.renew
  assert(!result[:changed] && !result[:reload_required], 'Completed reload should leave no pending work')
  unchanged_authority(state)
  checks << 'actual TLS 1.3 listener serves new full chain and acknowledges pending reload'
end
puts JSON.generate(ok: true, checks: checks)
