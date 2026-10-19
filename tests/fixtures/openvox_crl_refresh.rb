require 'tmpdir'
require 'socket'
load ARGV.shift

SCENARIOS = %w[valid revoked_intermediate expired future tampered missing duplicate conflict older_number older_timestamp online_ttl issuer_lifetime rollback failed_fetch running_consumer healthy stale near_expiry future_status failed_status publisher publisher_idempotent publisher_failure publisher_revoked lock_held cached_fallback expired_cache missing_cache failed_output root_failure root_tampered root_rollback sync_outage login_http login_blank_credentials login_missing_file login_blank_token login_tls_valid login_tls_revoked_peer login_tls_revoked_issuer login_tls_missing_crl login_tls_wrong_hostname malicious_issuer cached_revoked].freeze

def assert(value, message)
  raise message unless value
end

def rejected
  yield
rescue OpenVoxCRL::Invalid, OpenSSL::OpenSSLError => error
  assert(!error.message.empty?, 'Missing validation diagnostic')
else
  raise 'Invalid input was accepted'
end

def certificate(name, key, issuer = nil, signer = key, serial: 1, ca: true)
  cert = OpenSSL::X509::Certificate.new
  cert.version = 2
  cert.serial = serial
  cert.subject = OpenSSL::X509::Name.parse("/CN=#{name}")
  cert.issuer = issuer ? issuer.subject : cert.subject
  cert.public_key = key
  cert.not_before = Time.now - 3600
  cert.not_after = Time.now + (issuer ? 7 : 30) * 86400
  extensions = OpenSSL::X509::ExtensionFactory.new
  extensions.subject_certificate = cert
  extensions.issuer_certificate = issuer || cert
  cert.add_extension(extensions.create_extension('basicConstraints', ca ? "CA:TRUE,pathlen:#{issuer ? 0 : 1}" : 'CA:FALSE', true))
  cert.add_extension(extensions.create_extension('keyUsage', ca ? 'keyCertSign,cRLSign' : 'digitalSignature', true))
  cert.add_extension(extensions.create_extension('subjectKeyIdentifier', 'hash'))
  cert.add_extension(extensions.create_extension('authorityKeyIdentifier', 'keyid:always'))
  cert.sign(signer, nil)
  cert
end

def crl(cert, signer, number: 10, last_update: Time.now - 60, next_update: Time.now + 48 * 3600, revoked: nil)
  list = OpenSSL::X509::CRL.new
  list.version = 1
  list.issuer = cert.subject
  list.last_update = last_update
  list.next_update = next_update
  list.add_extension(OpenSSL::X509::Extension.new('crlNumber', OpenSSL::ASN1::Integer.new(number).to_der))
  if revoked
    item = OpenSSL::X509::Revoked.new
    item.serial = revoked
    item.time = Time.now - 60
    list.add_revoked(item)
  end
  list.sign(signer, nil)
  list
end

def scenario(name)
  root_key = OpenSSL::PKey.generate_key('ML-DSA-44')
  issuer_key = OpenSSL::PKey.generate_key('ML-DSA-44')
  root = certificate('Fixture root', root_key)
  issuer = certificate('Fixture issuer', issuer_key, root, root_key, serial: 2)
  root_crl = crl(root, root_key)
  issuer_crl = crl(issuer, issuer_key)
  authorities = [issuer, root]
  original = issuer_crl.to_pem + root_crl.to_pem
  lists, history = OpenVoxCRL.bundle(original, authorities, {})
  assert(lists.map(&:issuer) == authorities.map(&:subject), 'CRL order differs from authority order')
  request_method = OpenVoxCRL.method(:request)
  atomic_method = OpenVoxCRL.method(:atomic)
  Dir.mktmpdir('crl-fixture-') do |directory|
    root_path = "#{directory}/root.pem"
    issuer_path = "#{directory}/issuer.pem"
    File.write(root_path, root.to_pem)
    File.write(issuer_path, issuer.to_pem + root.to_pem)
    config = {'root' => root_path, 'issuer' => issuer_path, 'source_url' => 'http://fixture.invalid/openvox-crls.pem', 'root_crl_url' => 'http://fixture.invalid/root.crl.pem',
              'output_file' => "#{directory}/consumer.pem", 'state_file' => "#{directory}/history.json", 'status_file' => "#{directory}/status.json"}
    case name
    when 'valid'
      assert(OpenVoxCRL.root(root_path).to_der == root.to_der, 'Root mismatch')
      assert(OpenVoxCRL.issuer(issuer_path, root).to_der == issuer.to_der, 'Issuer mismatch')
      assert(OpenVoxCRL.bundle(root_crl.to_pem + issuer_crl.to_pem, authorities, history).last == history, 'Reordered unchanged bundle must be idempotent')
    when 'revoked_intermediate'
      revoked_root = crl(root, root_key, number: 11, revoked: issuer.serial)
      updated, = OpenVoxCRL.bundle(issuer_crl.to_pem + revoked_root.to_pem, authorities, history)
      store = OpenSSL::X509::Store.new
      store.add_cert(root)
      updated.each { |item| store.add_crl(item) }
      store.flags = OpenSSL::X509::V_FLAG_CRL_CHECK | OpenSSL::X509::V_FLAG_CRL_CHECK_ALL
      leaf_key = OpenSSL::PKey.generate_key('ML-DSA-44')
      leaf = certificate('Fixture leaf', leaf_key, issuer, issuer_key, serial: 3, ca: false)
      assert(!store.verify(leaf, [issuer]), 'Revoked issuer accepted by certificate store')
      assert(store.error == OpenSSL::X509::V_ERR_CERT_REVOKED, "Unexpected verification error: #{store.error_string}")
    when 'expired', 'future', 'online_ttl', 'issuer_lifetime', 'older_timestamp'
      arguments = case name
                  when 'expired' then {next_update: Time.now - 1}
                  when 'future' then {last_update: Time.now + 60}
                  when 'online_ttl' then {next_update: Time.now + 73 * 3600}
                  when 'issuer_lifetime' then {next_update: issuer.not_after + 1}
                  else {last_update: Time.now - 120, number: 11}
                  end
      changed = crl(issuer, issuer_key, **arguments)
      rejected { OpenVoxCRL.bundle(changed.to_pem + root_crl.to_pem, authorities, history) }
    when 'tampered'
      der = issuer_crl.to_der
      der.setbyte(der.bytesize - 1, der.getbyte(der.bytesize - 1) ^ 1)
      rejected { OpenVoxCRL.bundle(OpenSSL::X509::CRL.new(der).to_pem + root_crl.to_pem, authorities, history) }
    when 'missing'
      rejected { OpenVoxCRL.bundle(issuer_crl.to_pem, authorities, history) }
    when 'duplicate'
      rejected { OpenVoxCRL.bundle(issuer_crl.to_pem * 2, authorities, history) }
    when 'conflict'
      changed = crl(issuer, issuer_key, revoked: 123)
      rejected { OpenVoxCRL.bundle(changed.to_pem + root_crl.to_pem, authorities, history) }
    when 'older_number'
      changed = crl(issuer, issuer_key, number: 9)
      rejected { OpenVoxCRL.bundle(changed.to_pem + root_crl.to_pem, authorities, history) }
    when 'rollback', 'failed_fetch', 'running_consumer'
      response = original
      OpenVoxCRL.define_singleton_method(:request) { |url, **_options| url == config['root_crl_url'] ? root_crl.to_pem : response }
      OpenVoxCRL.run('sync', config)
      first_output = File.binread(config['output_file'])
      first_history = File.binread(config['state_file'])
      first_inode = File.stat(config['output_file']).ino
      if name == 'rollback'
        response = crl(issuer, issuer_key, number: 9).to_pem + root_crl.to_pem
        rejected { OpenVoxCRL.run('sync', config) }
      elsif name == 'failed_fetch'
        OpenVoxCRL.define_singleton_method(:request) do |url, **options|
          url == config['root_crl_url'] ? root_crl.to_pem : request_method.call(url, **options)
        end
        server = TCPServer.new('127.0.0.1', 0)
        config['source_url'] = "http://127.0.0.1:#{server.addr[1]}/crl"
        thread = Thread.new do
          client = server.accept
          client.gets
          client.write("HTTP/1.1 503 Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
          client.close
        end
        begin
          rejected { OpenVoxCRL.run('sync', config) }
        ensure
          thread.join
          server.close
        end
      else
        leaf_key = OpenSSL::PKey.generate_key('ML-DSA-44')
        leaf = certificate('Running consumer peer', leaf_key, issuer, issuer_key, serial: 123, ca: false)
        verify_peer = lambda do
          store = OpenSSL::X509::Store.new
          store.add_cert(root)
          OpenVoxCRL.parse_crls(File.read(config['output_file'])).each { |item| store.add_crl(item) }
          store.flags = OpenSSL::X509::V_FLAG_CRL_CHECK | OpenSSL::X509::V_FLAG_CRL_CHECK_ALL
          [store.verify(leaf, [issuer]), store.error]
        end
        assert(verify_peer.call.first, 'Initial consumer trust rejected the valid peer')
        response = crl(issuer, issuer_key, number: 11, revoked: 123).to_pem + root_crl.to_pem
        OpenVoxCRL.run('sync', config)
        assert(verify_peer.call == [false, OpenSSL::X509::V_ERR_CERT_REVOKED], 'Running consumer did not reject the newly revoked peer')
        assert(File.stat(config['output_file']).ino != first_inode, 'Consumer bundle was not atomically replaced')
        current = OpenVoxCRL.parse_crls(File.read(config['output_file'])).first
        assert(OpenVoxCRL.number(current) == 11 && current.revoked.first.serial == 123, 'Running consumer did not observe the new revocation')
        assert(File.binread(config['state_file']) != first_history, 'New CRL did not advance persistent history')
        OpenVoxCRL.check(config['status_file'])
      end
      unless name == 'running_consumer'
        assert(File.binread(config['output_file']) == first_output, 'Rejected refresh replaced the last good bundle')
        assert(File.binread(config['state_file']) == first_history, 'Rejected refresh replaced rollback history')
        rejected { OpenVoxCRL.check(config['status_file']) }
      end
    when 'root_failure', 'root_tampered', 'root_rollback', 'sync_outage', 'malicious_issuer'
      response = original
      root_response = root_crl.to_pem
      failure = false
      source_calls = 0
      OpenVoxCRL.define_singleton_method(:request) do |url, **options|
        assert(!options[:token] && !options[:cert] && !options[:credentials], 'Public download included authentication')
        if url == config['root_crl_url']
          raise OpenVoxCRL::Invalid, 'Fixture root endpoint failed' if failure && name == 'root_failure'
          next root_response
        end
        source_calls += 1
        next 'malformed compromised issuer response' if failure && name == 'malicious_issuer'
        raise OpenVoxCRL::Invalid, 'Fixture OpenVox endpoint failed' if failure && name == 'sync_outage'
        response
      end
      OpenVoxCRL.run('sync', config)
      before = %w[output_file state_file].to_h { |key| [key, File.binread(config[key])] }
      failure = true
      source_calls = 0
      if name == 'root_tampered'
        der = root_crl.to_der
        der.setbyte(der.bytesize - 1, der.getbyte(der.bytesize - 1) ^ 1)
        root_response = OpenSSL::X509::CRL.new(der).to_pem
      elsif name == 'root_rollback'
        root_response = crl(root, root_key, number: 9).to_pem
      elsif %w[sync_outage malicious_issuer].include?(name)
        root_response = crl(root, root_key, number: 11, revoked: issuer.serial).to_pem
      end
      rejected { OpenVoxCRL.run('sync', config) }
      rejected { OpenVoxCRL.check(config['status_file']) }
      if %w[sync_outage malicious_issuer].include?(name)
        assert(source_calls.zero?, 'Revoked issuer was contacted despite a valid cached CRL')
        updated, = OpenVoxCRL.bundle(File.read(config['output_file']), authorities, history)
        assert(updated.last.to_pem == root_response, 'Issuer outage prevented current root installation')
        store = OpenSSL::X509::Store.new
        store.add_cert(root)
        updated.each { |item| store.add_crl(item) }
        store.flags = OpenSSL::X509::V_FLAG_CRL_CHECK | OpenSSL::X509::V_FLAG_CRL_CHECK_ALL
        leaf_key = OpenSSL::PKey.generate_key('ML-DSA-44')
        leaf = certificate('Outage peer', leaf_key, issuer, issuer_key, serial: 8, ca: false)
        assert(!store.verify(leaf, [issuer]) && store.error == OpenSSL::X509::V_ERR_CERT_REVOKED, 'Current root revocation was not enforced during issuer outage')
      else
        before.each { |key, bytes| assert(File.binread(config[key]) == bytes, 'Invalid root response changed trusted files') }
      end
    when 'login_http', 'login_blank_credentials', 'login_missing_file', 'login_blank_token', 'login_tls_valid', 'login_tls_revoked_peer', 'login_tls_revoked_issuer', 'login_tls_missing_crl', 'login_tls_wrong_hostname'
      config.merge!('role_id_file' => "#{directory}/role-id", 'secret_id_file' => "#{directory}/secret-id", 'vault_url' => 'http://127.0.0.1:1')
      File.write(config['role_id_file'], 'fixture-role-id')
      File.write(config['secret_id_file'], 'fixture-secret-id')
      if name == 'login_http'
        rejected { OpenVoxCRL.login(config, root, [issuer_crl, root_crl]) }
      elsif name == 'login_blank_credentials'
        File.write(config['role_id_file'], "  ")
        OpenVoxCRL.define_singleton_method(:request) { |*_args, **_options| raise 'Missing credentials caused network access' }
        rejected { OpenVoxCRL.login(config, root, [issuer_crl, root_crl]) }
      elsif name == 'login_missing_file'
        File.unlink(config['secret_id_file'])
        OpenVoxCRL.define_singleton_method(:request) { |*_args, **_options| raise 'Missing credential file caused network access' }
        begin
          OpenVoxCRL.login(config, root, [issuer_crl, root_crl])
        rescue Errno::ENOENT
        else
          raise 'Missing credential file accepted'
        end
      elsif name == 'login_blank_token'
        OpenVoxCRL.define_singleton_method(:request) { |*_args, **_options| JSON.generate('auth' => {'client_token' => ''}) }
        rejected { OpenVoxCRL.login(config, root, [issuer_crl, root_crl]) }
      else
        leaf_key = OpenSSL::PKey.generate_key('ML-DSA-44')
        leaf = certificate(name == 'login_tls_wrong_hostname' ? 'wrong.example' : 'localhost', leaf_key, issuer, issuer_key, serial: 9, ca: false)
        context = OpenSSL::SSL::SSLContext.new
        context.min_version = OpenSSL::SSL::TLS1_3_VERSION
        context.cert = leaf
        context.key = leaf_key
        context.extra_chain_cert = [issuer]
        listener = TCPServer.new('127.0.0.1', 0)
        server = OpenSSL::SSL::SSLServer.new(listener, context)
        received = []
        config['vault_url'] = "https://localhost:#{listener.addr[1]}"
        thread = Thread.new do
          client = nil
          begin
            client = server.accept
            request_line = client.gets
            headers = {}
            while (line = client.gets) && line != "\r\n"
              key, value = line.split(':', 2)
              headers[key.downcase] = value.strip
            end
            received << [request_line, client.read(headers.fetch('content-length').to_i)]
            body = JSON.generate('auth' => {'client_token' => 'fixture-live-batch-token'})
            client.write("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: #{body.bytesize}\r\nConnection: close\r\n\r\n#{body}")
          rescue OpenSSL::SSL::SSLError
            # A rejected TLS handshake must deliver no application request.
          ensure
            client&.close
          end
        end
        lists = [issuer_crl, root_crl]
        lists[0] = crl(issuer, issuer_key, number: 11, revoked: leaf.serial) if name == 'login_tls_revoked_peer'
        lists[1] = crl(root, root_key, number: 11, revoked: issuer.serial) if name == 'login_tls_revoked_issuer'
        lists.pop if name == 'login_tls_missing_crl'
        begin
          if name == 'login_tls_valid'
            assert(OpenVoxCRL.login(config, root, lists) == 'fixture-live-batch-token', 'Verified AppRole login failed')
          else
            rejected { OpenVoxCRL.login(config, root, lists) }
          end
          assert(thread.join(5), 'TLS fixture did not finish')
          if name == 'login_tls_valid'
            assert(received.length == 1 && received.first.first.start_with?('POST /v1/auth/approle/login '), 'Wrong login HTTP request')
            assert(JSON.parse(received.first.last) == {'role_id' => 'fixture-role-id', 'secret_id' => 'fixture-secret-id'}, 'Verified login credentials differ')
          else
            assert(received.empty?, 'Untrusted TLS peer received AppRole credentials')
          end
        ensure
          listener.close
          thread.kill if thread.alive?
          thread.join
        end
      end
    when 'failed_output'
      response = original
      OpenVoxCRL.define_singleton_method(:request) { |url, **_options| url == config['root_crl_url'] ? root_crl.to_pem : response }
      OpenVoxCRL.run('sync', config)
      previous_output = File.binread(config['output_file'])
      newer = crl(issuer, issuer_key, number: 11, revoked: 123).to_pem + root_crl.to_pem
      response = newer
      OpenVoxCRL.define_singleton_method(:atomic) do |path, bytes|
        raise OpenVoxCRL::Invalid, 'Fixture output replacement failed' if path == config['output_file']
        atomic_method.call(path, bytes)
      end
      rejected { OpenVoxCRL.run('sync', config) }
      assert(File.binread(config['output_file']) == previous_output, 'Failed output replacement destroyed the old bundle')
      issuer_id = Digest::SHA256.hexdigest(issuer.public_key.public_to_der)
      assert(OpenVoxCRL.read_json(config['state_file']).fetch(issuer_id).fetch('number') == 11, 'New rollback barrier was not durable before output replacement')
      rejected { OpenVoxCRL.check(config['status_file']) }
      OpenVoxCRL.define_singleton_method(:atomic, atomic_method)
      response = original
      rejected { OpenVoxCRL.run('sync', config) }
      assert(File.binread(config['output_file']) == previous_output, 'Rollback retry replaced the valid previous output')
      response = newer
      OpenVoxCRL.run('sync', config)
      assert(File.binread(config['output_file']) == newer, 'Retry did not install the already recorded newer CRL')
      OpenVoxCRL.check(config['status_file'])
    when 'lock_held'
      OpenVoxCRL.define_singleton_method(:request) { |url, **_options| url == config['root_crl_url'] ? root_crl.to_pem : original }
      OpenVoxCRL.run('sync', config)
      before = %w[output_file state_file status_file].to_h { |key| [key, File.binread(config[key])] }
      File.open("#{config['state_file']}.lock", File::RDWR) do |lock|
        assert(lock.flock(File::LOCK_EX | File::LOCK_NB), 'Fixture could not acquire updater lock')
        rejected { OpenVoxCRL.run('sync', config) }
        before.each do |key, bytes|
          assert(File.binread(config[key]) == bytes, "Concurrent invocation changed #{key}")
        end
        OpenVoxCRL.check(config['status_file'])
      end
      OpenVoxCRL.run('sync', config)
      OpenVoxCRL.check(config['status_file'])
    when 'publisher', 'publisher_idempotent', 'publisher_failure', 'publisher_revoked', 'cached_fallback', 'expired_cache', 'missing_cache', 'cached_revoked'
      vault_key = OpenSSL::PKey.generate_key('ML-DSA-44')
      vault = certificate('Fixture Vault issuer', vault_key, root, root_key, serial: 3)
      vault_crl = crl(vault, vault_key)
      incoming = crl(root, root_key, number: 11, revoked: name == 'cached_revoked' ? issuer.serial : (name == 'publisher_revoked' ? vault.serial : nil))
      vault_path = "#{directory}/vault.pem"
      File.write(vault_path, vault.to_pem)
      File.write("#{directory}/role-id", 'fixture-role-id')
      File.write("#{directory}/secret-id", 'fixture-secret-id')
      config.merge!('openvox_issuer' => issuer_path, 'vault_issuer' => vault_path,
                    'role_id_file' => "#{directory}/role-id", 'secret_id_file' => "#{directory}/secret-id",
                    'output_dir' => "#{directory}/public", 'trust_dir' => "#{directory}/trust",
                    'openvox_url' => 'https://openvox.invalid', 'vault_url' => 'https://vault.invalid',
                    'client_cert' => "#{directory}/client.pem", 'client_key' => "#{directory}/client.key")
      if %w[cached_fallback expired_cache missing_cache cached_revoked].include?(name)
        output = config['output_dir']
        FileUtils.mkdir_p(output)
        FileUtils.mkdir_p(config['trust_dir'])
        File.write("#{directory}/trust/root.crl", root_crl.to_pem)
        File.write(config['state_file'], JSON.generate(history))
        unless name == 'missing_cache'
          cached_issuer = name == 'expired_cache' ? crl(issuer, issuer_key, next_update: Time.now - 1) : issuer_crl
          File.write("#{output}/openvox-crls.pem", cached_issuer.to_pem + root_crl.to_pem)
          File.write("#{output}/vault.crl.pem", vault_crl.to_pem)
        end
        before = Dir.glob("#{output}/*").to_h { |path| [path, File.binread(path)] }
        previous_history = File.read(config['state_file'])
        issuer_requests = 0
        login_requests = 0
        uploaded = {}
        OpenVoxCRL.define_singleton_method(:request) do |url, **options|
          if url.end_with?('/v1/auth/approle/login')
            login_requests += 1
            assert(options[:credentials] && options[:method] == 'POST', 'Outage login lacks credential guard')
            assert(options[:root].to_der == root.to_der && options[:crls].map(&:to_der) == [vault_crl, incoming].map(&:to_der), 'Outage login lacks current full-chain trust')
            raise OpenSSL::SSL::SSLError, 'Fixture Vault TLS unavailable' if name == 'cached_fallback'
            next JSON.generate('auth' => {'client_token' => 'fixture-outage-batch-token'})
          elsif url.include?('/v1/auth/cert/crls/')
            assert(name == 'cached_revoked' && options[:method] == 'POST' && options[:token] == 'fixture-outage-batch-token', 'Incorrect outage CRL upload')
            uploaded[url.split('/').last] = JSON.parse(options[:data]).fetch('crl')
            next '{}'
          elsif url.end_with?('/puppet-ca/v1/certificate_revocation_list')
            raise 'Publisher attempted to update a revoked issuer'
          end
          assert(!options[:token] && !options[:cert] && !options[:credentials], 'Public download leaked credentials')
          next incoming.to_pem if url == config['root_crl_url']
          if name == 'cached_revoked'
            next vault_crl.to_pem if url.end_with?('/v1/pki_int/crl/pem')
            issuer_requests += 1
            next 'malformed compromised issuer response'
          end
          raise OpenVoxCRL::Invalid, 'Fixture issuer offline'
        end
        if name == 'missing_cache'
          begin
            OpenVoxCRL.run('publish', config)
          rescue Errno::ENOENT
            # A first publication needs signed issuer evidence and cannot fabricate it.
          else
            raise 'Publisher accepted missing issuer cache'
          end
        else
          rejected { OpenVoxCRL.run('publish', config) }
        end
        rejected { OpenVoxCRL.check(config['status_file']) }
        if %w[cached_fallback cached_revoked].include?(name)
          assert(login_requests == 1, 'Publisher did not attempt guarded Vault synchronization during issuer outage')
          if name == 'cached_revoked'
            assert(issuer_requests.zero?, 'Publisher contacted the revoked issuer instead of using its valid cache')
            assert(uploaded == {'openvox' => issuer_crl.to_pem, 'root' => incoming.to_pem}, 'Healthy Vault did not receive the root revoking the OpenVox issuer')
          else
            assert(uploaded.empty?, 'Failed TLS login proceeded to CRL uploads')
          end
          assert(File.read("#{directory}/trust/root.crl") == incoming.to_pem, 'Outage blocked the incoming signed root CRL')
          refreshed, = OpenVoxCRL.bundle(File.read("#{output}/openvox-crls.pem"), [issuer, root], history)
          assert(refreshed.last.to_der == incoming.to_der, 'Consumer bundle kept the old root CRL during outage')
          assert(File.read(config['state_file']) != previous_history, 'Outage publication did not advance root rollback protection')
        else
          before.each { |path, bytes| assert(File.binread(path) == bytes, 'Invalid or missing cache replaced public artifacts') }
          assert(File.read(config['state_file']) == previous_history, 'Invalid or missing cache changed rollback history')
        end
        return
      end
      requests = []
      server_root = root_crl
      login_count = 0
      batch_token = nil
      OpenVoxCRL.define_singleton_method(:request) do |url, **options|
        requests << [url, options]
        if url == config['root_crl_url']
          assert(!options[:token] && !options[:cert] && !options[:credentials], 'Public root request leaked credentials')
          next incoming.to_pem
        end
        case URI(url).path
        when '/puppet-ca/v1/certificate_revocation_list/ca'
          issuer_crl.to_pem + server_root.to_pem
        when '/v1/pki_int/crl/pem'
          vault_crl.to_pem
        when '/v1/auth/approle/login'
          assert(options[:credentials] && options[:method] == 'POST', 'AppRole login missing credential guard')
          assert(JSON.parse(options[:data]) == {'role_id' => 'fixture-role-id', 'secret_id' => 'fixture-secret-id'}, 'AppRole payload differs')
          assert(options[:root].to_der == root.to_der, 'Vault request uses the wrong trust root')
          assert(options[:crls].map(&:issuer) == [vault.subject, root.subject], 'Login lacks full chain CRLs')
          assert(File.read("#{directory}/trust/root.crl") == incoming.to_pem, 'New root CRL was not published before credentialed login')
          if name == 'publisher_revoked'
            store = OpenSSL::X509::Store.new
            store.add_cert(root)
            options[:crls].each { |item| store.add_crl(item) }
            store.flags = OpenSSL::X509::V_FLAG_CRL_CHECK | OpenSSL::X509::V_FLAG_CRL_CHECK_ALL
            assert(!store.verify(vault) && store.error == OpenSSL::X509::V_ERR_CERT_REVOKED, 'Revoked Vault issuer did not fail verification')
            raise OpenVoxCRL::Invalid, 'Fixture TLS denies revoked Vault authority'
          end
          login_count += 1
          batch_token = "fixture-batch-token-#{login_count}"
          JSON.generate({'auth' => {'client_token' => batch_token}})
        when '/v1/auth/cert/crls/openvox', '/v1/auth/cert/crls/root'
          assert(options[:method] == 'POST' && options[:token] == batch_token, 'CRL auth update missing method/token')
          expected = url.end_with?('/root') ? incoming : issuer_crl
          assert(JSON.parse(options[:data]) == {'crl' => expected.to_pem}, 'Vault auth CRL payload differs')
          raise OpenVoxCRL::Invalid, 'Fixture API unavailable' if name == 'publisher_failure' && url.end_with?('/root')
          '{}'
        when '/puppet-ca/v1/certificate_revocation_list'
          assert(options[:method] == 'PUT' && options[:content_type] == 'text/plain', 'Puppet root API requires raw PEM')
          assert(options[:data] == incoming.to_pem, 'Puppet root API received wrapped or wrong CRL')
          assert(options[:cert] == config['client_cert'] && options[:key] == config['client_key'], 'Puppet update lacks client authentication')
          assert(options[:crls].map(&:issuer) == [issuer.subject, root.subject], 'Puppet update lacks full chain CRLs')
          server_root = OpenSSL::X509::CRL.new(options[:data])
          ''
        else
          raise "Unexpected request #{url}"
        end
      end
      if %w[publisher_failure publisher_revoked].include?(name)
        rejected { OpenVoxCRL.run('publish', config) }
        rejected { OpenVoxCRL.check(config['status_file']) }
        assert(File.read("#{directory}/trust/root.crl") == incoming.to_pem, 'Partial failure lost newly published emergency root CRL')
        assert(OpenVoxCRL.read_json(config['state_file']).values.any? { |item| item['number'] == 11 }, 'Partial failure lost rollback protection')
      else
        OpenVoxCRL.run('publish', config)
        OpenVoxCRL.run('publish', config) if name == 'publisher_idempotent'
        updates = requests.count { |url, options| url.end_with?('/certificate_revocation_list') && options[:method] == 'PUT' }
        assert(updates == 1, 'Repeated publication retried an identical root CRL PUT')
        assert(login_count == (name == 'publisher_idempotent' ? 2 : 1), 'Each publication must obtain a fresh batch token')
        OpenVoxCRL.check(config['status_file'])
      end
      assert(File.read("#{directory}/public/openvox-crls.pem") == issuer_crl.to_pem + incoming.to_pem, 'Cached OpenVox bundle differs')
      assert(File.read("#{directory}/public/vault.crl.pem") == vault_crl.to_pem, 'Cached Vault issuer CRL differs')
      assert(File.read("#{directory}/trust/outbound-crls.pem") == vault_crl.to_pem + incoming.to_pem, 'Local outbound bundle differs')
      assert(File.read("#{directory}/trust/root.crl") == incoming.to_pem, 'Local root handoff is not PEM')
      generated = Dir.glob("#{directory}/{public,trust}/*") + [config['state_file'], config['status_file']]
      generated.each do |path|
        assert(!File.read(path).match?(/fixture-(role-id|secret-id|batch-token)/), 'Generated artifact contains credentials')
      end
    when 'healthy', 'stale', 'near_expiry', 'future_status', 'failed_status'
      now = Time.now.to_i
      status = {'ok' => name != 'failed_status', 'last_success' => now, 'next_expiry' => now + 48 * 3600}
      status['last_success'] = now - 901 if name == 'stale'
      status['last_success'] = now + 60 if name == 'future_status'
      status['next_expiry'] = now + 23 * 3600 if name == 'near_expiry'
      File.write(config['status_file'], JSON.generate(status))
      if name == 'healthy'
        OpenVoxCRL.check(config['status_file'])
      else
        rejected { OpenVoxCRL.check(config['status_file']) }
      end
    else
      raise "Unknown scenario #{name}"
    end
  end
ensure
  OpenVoxCRL.define_singleton_method(:request, request_method) if request_method
  OpenVoxCRL.define_singleton_method(:atomic, atomic_method) if atomic_method
end

(ARGV.empty? ? SCENARIOS : ARGV).each do |name|
  scenario(name)
  puts "PASS #{name}"
end
