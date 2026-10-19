require 'openssl'
require 'socket'
require 'net/http'
require 'json'
require 'yaml'
require 'open3'
require 'timeout'

# Run only in the disposable server: the real autosigner and Hiera meet controlled peers.
HOST = 'openvox.rehearsal.test'
SSL = '/etc/puppetlabs/puppet/ssl'
CA = '/etc/puppetlabs/puppetserver/ca'
HIERA = '/etc/puppetlabs/code/rehearsal-environments/production/hiera.yaml'
PORT = 9443
RESULTS = {}
TRACES = []

def server_request(method, path)
  http = Net::HTTP.new(HOST, 8140, nil)
  http.use_ssl = true
  http.ca_file = '/trust/root.pem'
  http.cert = OpenSSL::X509::Certificate.new(File.read("#{SSL}/certs/#{HOST}.pem"))
  http.key = OpenSSL::PKey.read(File.read("#{SSL}/private_keys/#{HOST}.pem"))
  http.open_timeout = 5
  http.read_timeout = 30
  http.request(Net::HTTP.const_get(method).new(path))
end

def fixture_certificate(scenario, issuer, issuer_key)
  key = scenario == 'tls12_only' ? OpenSSL::PKey::RSA.new(2048) : OpenSSL::PKey.generate_key('ML-DSA-44')
  cert = OpenSSL::X509::Certificate.new
  cert.version = 2
  cert.serial = OpenSSL::BN.rand(128).to_i
  name = scenario == 'wrong_hostname' ? 'wrong.rehearsal.test' : HOST
  cert.subject = OpenSSL::X509::Name.parse("/CN=#{name}")
  cert.issuer = scenario == 'untrusted' ? cert.subject : issuer.subject
  cert.public_key = key
  cert.not_before = Time.now - 3600
  cert.not_after = Time.now + (scenario == 'expired' ? -60 : 3600)
  extensions = OpenSSL::X509::ExtensionFactory.new
  extensions.subject_certificate = cert
  extensions.issuer_certificate = scenario == 'untrusted' ? cert : issuer
  cert.add_extension(extensions.create_extension('basicConstraints', 'CA:FALSE', true))
  cert.add_extension(extensions.create_extension('keyUsage', 'digitalSignature', true))
  cert.add_extension(extensions.create_extension('extendedKeyUsage', 'serverAuth'))
  cert.add_extension(extensions.create_extension('subjectAltName', "DNS:#{name}"))
  cert.add_extension(extensions.create_extension('subjectKeyIdentifier', 'hash'))
  cert.add_extension(extensions.create_extension('authorityKeyIdentifier', 'keyid:always')) unless scenario == 'untrusted'
  cert.sign(scenario == 'untrusted' ? key : issuer_key, nil)
  [cert, key]
end

def python_autosigner
  program = <<~PYTHON
    import json, sys
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import mldsa
    from cryptography.x509.oid import NameOID
    sys.path.insert(0, '/helpers')
    from autosign import validate_csr
    builder = x509.CertificateSigningRequestBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'rehearsal.home.arpa')]))
    builder = builder.add_attribute(x509.ObjectIdentifier('1.2.840.113549.1.9.7'), b'disposable-transport-fixture')
    csr = builder.sign(mldsa.MLDSA44PrivateKey.generate(), None).public_bytes(serialization.Encoding.PEM)
    try:
        validate_csr('rehearsal.home.arpa', csr, 'https://openvox.rehearsal.test:9443', '/trust/root.pem', 'home.arpa')
    except Exception as error:
        print(json.dumps({'accepted': False, 'exception': type(error).__name__}))
    else:
        print(json.dumps({'accepted': True}))
  PYTHON
  output, _error, status = Open3.capture3({ 'OPENSSL_CONF' => '/rehearsal/openssl.cnf' }, '/opt/puppetlabs/puppet/venv/bin/python', '-c', program)
  raise 'Python fixture process failed' unless status.success?

  JSON.parse(output)
end

original = File.read(HIERA)
issuer = OpenSSL::X509::Certificate.new(File.read("#{CA}/ca_crt.pem"))
issuer_key = OpenSSL::PKey.read(File.read("#{CA}/ca_key.pem"))
begin
  %w[valid classical_only tls12_only wrong_hostname untrusted expired].each do |scenario|
    cert, key = fixture_certificate(scenario, issuer, issuer_key)
    context = OpenSSL::SSL::SSLContext.new
    context.cert, context.key = cert, key
    context.extra_chain_cert = [issuer]
    context.ca_file = '/trust/root.pem'
    context.verify_mode = OpenSSL::SSL::VERIFY_PEER
    context.min_version = context.max_version = scenario == 'tls12_only' ? OpenSSL::SSL::TLS1_2_VERSION : OpenSSL::SSL::TLS1_3_VERSION
    context.ecdh_curves = scenario == 'classical_only' ? 'X25519' : 'X25519MLKEM768:X25519'
    listener = TCPServer.new('0.0.0.0', PORT)
    records = []
    errors = []
    worker = Thread.new do
      loop do
        tcp = listener.accept
        socket = OpenSSL::SSL::SSLSocket.new(tcp, context)
        socket.sync_close = true
        begin
          Timeout.timeout(15) do
            socket.accept
            line = socket.gets
            next unless line

            content_length = 0
            while (header = socket.gets) && header != "\r\n"
              content_length = Integer(header.split(':', 2).last) if header.downcase.start_with?('content-length:')
            end
            socket.read(content_length) if content_length.positive?
            records << {
              scenario: scenario, path: line.split[1], protocol: socket.ssl_version,
              group: socket.group, cipher: socket.cipher.first, resumed: socket.session_reused?,
              client_signature: socket.peer_sigalg, server_signature: socket.sigalg,
              client_certificate: socket.peer_cert&.subject&.to_s
            }
            body = if line.include?('/auth/token/lookup-self')
                     { data: { policies: ['puppet-enrollment'], meta: { certname: 'rehearsal.home.arpa' } } }
                   elsif line.include?('/auth/cert/login')
                     { auth: { client_token: 'disposable-hiera-fixture' } }
                   else
                     { data: { data: { 'rehearsal::marker' => 'outbound-transport-verified' } } }
                   end.to_json
            socket.write("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: #{body.bytesize}\r\nConnection: close\r\n\r\n#{body}")
          end
        rescue OpenSSL::SSL::SSLError => error
          errors << error.message
        rescue IOError, SystemCallError, Timeout::Error => error
          errors << error.class.name
        ensure
          socket.close
        end
      end
    end
    begin
      python = python_autosigner
      fixture = YAML.safe_load(original)
      fixture.fetch('hierarchy').first['uris'] = ["https://#{HOST}:#{PORT}/v1/kv/data/puppet"]
      File.write(HIERA, YAML.dump(fixture))
      raise 'Cache invalidation failed' unless server_request('Delete', '/puppet-admin-api/v1/environment-cache?environment=production').code == '204'

      response = server_request('Get', "/puppet/v3/catalog/#{HOST}?environment=production")
      java_accepted = response.code == '200' && response.body.include?('outbound-transport-verified')
      results = {
        python_autosigner: python,
        jruby_hiera: { accepted: java_accepted, catalog_status: response.code.to_i },
        server_tls_errors: errors
      }
      %i[python_autosigner jruby_hiera].each do |transport|
        accepted = results.fetch(transport).fetch(:accepted) { results.fetch(transport).fetch('accepted') }
        tls_rejection = transport == :python_autosigner ? results[transport]['exception'] == 'SSLError' : response.code == '500'
        results["#{transport}_policy_passed"] = scenario == 'valid' ? accepted : !accepted && tls_rejection
        if scenario == 'tls12_only'
          # Signature rejection alone does not prove that the protocol floor applies.
          results["#{transport}_policy_passed"] &&= errors.length >= 2 && errors.all? do |error|
            error.match?(/unsupported protocol|protocol version/i)
          end
        end
      end
      RESULTS[scenario] = results
      TRACES.concat(records)
    ensure
      worker.kill
      worker.join
      listener.close
    end
  end
ensure
  File.write(HIERA, original)
  raise 'Restoring production cache failed' unless server_request('Delete', '/puppet-admin-api/v1/environment-cache?environment=production').code == '204'
end

positive = TRACES.select { |trace| trace[:scenario] == 'valid' }
RESULTS['positive_handshakes'] = {
  python_token_only: positive.any? { |trace| trace[:path].include?('lookup-self') && trace[:client_certificate].nil? },
  jruby_mtls: positive.any? { |trace| trace[:path].include?('auth/cert/login') && trace[:client_signature]&.delete('-_')&.casecmp?('mldsa44') },
  tls13_hybrid_mldsa: !positive.empty? && positive.all? do |trace|
    trace[:protocol] == 'TLSv1.3' && trace[:group] == 'X25519MLKEM768' &&
      trace[:resumed] == false && trace[:server_signature]&.delete('-_')&.casecmp?('mldsa44')
  end
}
puts JSON.generate(results: RESULTS, handshakes: TRACES)
