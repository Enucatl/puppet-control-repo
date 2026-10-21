require 'digest'
require 'json'
require 'openssl'
require 'socket'
require 'timeout'

# Use the native ML-DSA runtime for CSR signing and exact listener verification.
module TransportCertificate
  def self.certificates(pem)
    pem.scan(/-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----/m).map { |value| OpenSSL::X509::Certificate.new(value) }
  end

  def self.validate(data, previous, root, hostname, inspect_only = false)
    leaf = OpenSSL::X509::Certificate.new(data.fetch('certificate'))
    old = OpenSSL::X509::Certificate.new(previous.fetch('certificate'))
    key = OpenSSL::PKey.read(previous.fetch('private_key'))
    chain = certificates(data.fetch('ca_chain').join("\n"))
    raise 'Transport renewal changed the pinned root' unless chain.length == 2 && chain.last.to_der == root.to_der
    [leaf, *chain].each do |cert|
      raise 'Expected valid ML-DSA-44 transport chain' unless cert.signature_algorithm == 'ML-DSA-44' && cert.not_before <= Time.now && (cert.not_after > Time.now || (cert.equal?(leaf) && inspect_only))
    end
    [leaf, *chain, root].each_cons(2) do |cert, issuer|
      raise 'Invalid transport issuer or lifetime' unless cert.issuer.to_der == issuer.subject.to_der && cert.verify(issuer.public_key) && cert.not_after <= issuer.not_after
    end
    identity = lambda { |cert| cert.extensions.reject { |ext| %w[subjectKeyIdentifier authorityKeyIdentifier].include?(ext.oid) }.map(&:to_der).sort }
    raise 'Transport renewal changed the key or identity' unless
      leaf.public_key.public_to_der == key.public_to_der && leaf.subject.to_der == old.subject.to_der &&
      identity.call(leaf) == identity.call(old) && OpenSSL::SSL.verify_certificate_identity(leaf, hostname)
    [leaf, key]
  end

  def self.run(request)
    data = request.fetch('data')
    previous = request.fetch('previous', data)
    root = OpenSSL::X509::Certificate.new(File.read(request.fetch('root')))
    raise 'Root differs from the established fingerprint' unless Digest::SHA256.hexdigest(root.to_der) == request.fetch('root_sha256')
    hostname = request.fetch('hostname')
    leaf, key = validate(data, previous, root, hostname, request.fetch('inspect_only', false))
    if request['port']
      context = OpenSSL::SSL::SSLContext.new
      context.min_version = context.max_version = OpenSSL::SSL::TLS1_3_VERSION
      context.verify_mode = OpenSSL::SSL::VERIFY_PEER
      context.ca_file = request.fetch('root')
      Timeout.timeout(15) do
        Socket.tcp('127.0.0.1', request.fetch('port'), connect_timeout: 5) do |socket|
          connection = OpenSSL::SSL::SSLSocket.new(socket, context)
          begin
            connection.hostname = hostname
            connection.connect
            connection.post_connection_check(hostname)
            raise 'Listener has not loaded the saved certificate' unless connection.peer_cert.to_der == leaf.to_der
          ensure
            connection.close
          end
        end
      end
    end
    csr = OpenSSL::X509::Request.new
    csr.version = 0
    csr.subject = leaf.subject
    csr.public_key = key
    san = leaf.extensions.find { |ext| ext.oid == 'subjectAltName' }
    if san
      csr.add_attribute(OpenSSL::X509::Attribute.new('extReq', OpenSSL::ASN1::Set.new([OpenSSL::ASN1::Sequence.new([san])])) )
    end
    csr.sign(key, nil)
    {csr: csr.to_pem, due: leaf.not_after <= Time.now + request.fetch('window_days') * 86400,
     not_after: leaf.not_after.utc.to_s, public_key: key.public_to_pem}
  end
end

puts JSON.generate(TransportCertificate.run(JSON.parse(STDIN.read))) if $PROGRAM_NAME == __FILE__
