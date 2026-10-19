require 'digest'
require 'fileutils'
require 'json'
require 'open3'
require 'openssl'
require 'socket'
require 'timeout'

module OpenVoxRenewal
  HOST = 'openvox.home.arpa'
  SSL = '/etc/puppetlabs/puppet/ssl'
  CA = '/etc/puppetlabs/puppetserver/ca'
  TRUST = '/trust'
  STATE = '/opt/puppetlabs/server/data/puppetserver/server_data/bootstrap'
  DAY = 86400

  def self.certificates(pem)
    pem.scan(/-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----/m).map { |value| OpenSSL::X509::Certificate.new(value) }
  end

  def self.atomic_write(path, contents)
    temporary = path + '.tmp'
    File.open(temporary, File::WRONLY | File::CREAT | File::TRUNC, 0o600) do |file|
      file.write(contents)
      file.fsync
    end
    File.rename(temporary, path)
    File.open(File.dirname(path), &:fsync)
  end

  def self.verify_leaf(leaf, previous, key, chain)
    raise 'Unexpected authority chain' unless chain.length == 2
    [leaf, *chain].each do |cert|
      raise 'Expected valid ML-DSA-44 certificates' unless
        cert.signature_algorithm == 'ML-DSA-44' &&
        OpenSSL::ASN1.decode(cert.public_key.public_to_der).value[0].value[0].oid == '2.16.840.1.101.3.4.3.17' &&
        cert.not_before <= Time.now && cert.not_after > Time.now
    end
    [leaf, *chain].each_cons(2) do |cert, issuer|
      raise 'Invalid issuer signature or lifetime' unless
        cert.issuer.to_der == issuer.subject.to_der && cert.verify(issuer.public_key) &&
        cert.not_after <= issuer.not_after
    end
    raise 'Invalid root signature' unless chain.last.verify(chain.last.public_key) && chain.last.issuer.to_der == chain.last.subject.to_der
    identity_extensions = lambda { |cert| cert.extensions.reject { |ext| %w[subjectKeyIdentifier authorityKeyIdentifier].include?(ext.oid) }.map(&:to_der).sort }
    raise 'Renewal changed the key or certificate identity' unless
      leaf.public_key.public_to_der == key.public_to_der &&
      leaf.subject.to_der == previous.subject.to_der &&
      identity_extensions.call(leaf) == identity_extensions.call(previous) &&
      OpenSSL::SSL.verify_certificate_identity(leaf, HOST)
  end

  def self.load_state
    path = "#{SSL}/certs/#{HOST}.pem"
    bundle = certificates(File.read(path))
    chain = certificates(File.read("#{TRUST}/openvox-chain.pem"))
    root = certificates(File.read("#{TRUST}/root.pem"))
    raise 'Restore the established full certificate chain' unless
      root.length == 1 && chain.length == 2 && bundle.length == 3 &&
      bundle.drop(1).map(&:to_der) == chain.map(&:to_der) && chain.last.to_der == root.first.to_der
    key = OpenSSL::PKey.read(File.read("#{SSL}/private_keys/#{HOST}.pem"))
    verify_leaf(bundle.first, bundle.first, key, chain)
    [path, bundle.first, key, chain]
  end

  def self.run_builtin(candidate)
    output, error, status = Open3.capture3(
      '/opt/puppetlabs/bin/puppet', 'ssl', 'renew_cert', '--if-expiring-in', '30d',
      '--config', '/etc/puppetlabs/puppet/puppet.conf', '--hostcert', candidate,
      stdin_data: ''
    )
    warn(output + error) unless status.success?
    raise 'Built-in certificate renewal failed' unless status.success?
  end

  def self.renew
    File.umask(0o077)
    path, previous, key, chain = load_state
    FileUtils.mkdir_p(STATE, mode: 0o700)
    marker = "#{STATE}/openvox-reload.pending"
    candidate = "#{STATE}/openvox-renewal.pem"
    changed = false
    if previous.not_after <= Time.now + 30 * DAY
      raise 'Renew the established issuer before issuing another 60-day leaf' unless chain.first.not_after > Time.now + 60 * DAY
      staged = File.exist?(candidate) ? certificates(File.read(candidate)).first : nil
      # Reuse an interrupted, already-issued renewal without another CA request.
      reused = staged && staged.not_after > previous.not_after && staged.not_after > Time.now + 30 * DAY
      atomic_write(candidate, previous.to_pem) unless reused
      # The built-in no-op still validates a cached candidate's key, chain and CRL.
      run_builtin(candidate)
      staged = certificates(File.read(candidate)).first
      raise 'CA did not issue a 60-day renewal' unless staged && (reused || staged.not_after > Time.now + 59 * DAY)
      raise 'Staged renewal exceeds the 60-day policy' unless staged.not_after <= Time.now + 60 * DAY + 300
      verify_leaf(staged, previous, key, chain)
      raise 'Renewal did not change serial or extend validity' unless staged.serial != previous.serial && staged.not_after > previous.not_after
      # A crash after publication must still trigger a listener reload next run.
      atomic_write(marker, Digest::SHA256.hexdigest(staged.to_der))
      atomic_write(path, staged.to_pem + chain.map(&:to_pem).join)
      changed = true
      previous = staged
    end
    {changed: changed, reload_required: File.exist?(marker), serial: previous.serial.to_s(16),
     not_after: previous.not_after.utc.to_s, root_sha256: Digest::SHA256.hexdigest(chain.last.to_der),
     host_key_sha256: Digest::SHA256.file("#{SSL}/private_keys/#{HOST}.pem").hexdigest,
     ca_key_sha256: Digest::SHA256.file("#{CA}/ca_key.pem").hexdigest}
  end

  def self.verify
    _path, leaf, _key, chain = load_state
    expected = Digest::SHA256.hexdigest(leaf.to_der)
    context = OpenSSL::SSL::SSLContext.new
    context.min_version = context.max_version = OpenSSL::SSL::TLS1_3_VERSION
    context.verify_mode = OpenSSL::SSL::VERIFY_PEER
    context.ca_file = "#{TRUST}/root.pem"
    Timeout.timeout(10) do
      Socket.tcp('127.0.0.1', 8140, connect_timeout: 5) do |socket|
        connection = OpenSSL::SSL::SSLSocket.new(socket, context)
        begin
          connection.hostname = HOST
          connection.connect
          connection.post_connection_check(HOST)
          raise 'Listener still serves a different certificate' unless Digest::SHA256.hexdigest(connection.peer_cert.to_der) == expected
        ensure
          connection.close
        end
      end
    end
    marker = "#{STATE}/openvox-reload.pending"
    if File.exist?(marker)
      raise 'Pending listener identity does not match the live certificate' unless File.read(marker) == expected
      File.unlink(marker)
      File.open(STATE, &:fsync)
    end
    {listener_verified: true, serial: leaf.serial.to_s(16), not_after: leaf.not_after.utc.to_s,
     leaf_sha256: expected, root_sha256: Digest::SHA256.hexdigest(chain.last.to_der)}
  end
end

if $PROGRAM_NAME == __FILE__
  abort 'Usage: infra-renew.rb renew | verify' unless ARGV.length == 1 && %w[renew verify].include?(ARGV.first)
  puts JSON.generate(OpenVoxRenewal.public_send(ARGV.first))
end
