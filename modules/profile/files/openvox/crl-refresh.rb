#!/opt/puppetlabs/puppet/bin/ruby
# Public downloads carry no credentials; only pinned signatures authorize updates.
require 'openssl'
require 'net/http'
require 'json'
require 'digest'
require 'fileutils'
require 'tempfile'
require 'uri'

module OpenVoxCRL
  class Invalid < StandardError; end
  MAX_BYTES = 4 * 1024 * 1024
  ONLINE_TTL = 72 * 3600
  WARNING = 24 * 3600
  module_function

  def require_valid(value, message)
    raise Invalid, message unless value
  end

  def certificates(path)
    File.read(path).scan(/-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----/m).map do |pem|
      OpenSSL::X509::Certificate.new(pem)
    end
  end

  def mldsa?(certificate)
    certificate.signature_algorithm == 'ML-DSA-44' &&
      OpenSSL::ASN1.decode(certificate.public_key.public_to_der).value[0].value[0].oid == '2.16.840.1.101.3.4.3.17'
  end

  def authority(certificate)
    now = Time.now
    require_valid(mldsa?(certificate), 'Authority must use ML-DSA-44')
    require_valid(certificate.not_before <= now && now < certificate.not_after, 'Authority is not currently valid')
    require_valid(certificate.extensions.any? { |e| e.oid == 'basicConstraints' && e.critical? && e.value.include?('CA:TRUE') }, 'Authority lacks CA constraint')
    require_valid(certificate.extensions.any? { |e| e.oid == 'keyUsage' && e.value.include?('CRL Sign') }, 'Authority cannot sign CRLs')
    certificate
  end

  def root(path)
    roots = certificates(path).select { |c| c.subject.to_der == c.issuer.to_der && c.verify(c.public_key) }
    require_valid(roots.length == 1, 'Expected one pinned root')
    authority(roots.first)
  end

  def issuer(path, root)
    issuers = certificates(path).reject { |c| c.to_der == root.to_der }
    require_valid(issuers.length == 1, 'Expected one intermediate and optional pinned root')
    cert = authority(issuers.first)
    require_valid(cert.issuer.to_der == root.subject.to_der && cert.verify(root.public_key), 'Intermediate does not chain to pinned root')
    require_valid(cert.not_after <= root.not_after, 'Intermediate outlives root')
    cert
  end

  def parse_crls(bytes)
    require_valid(bytes.bytesize <= MAX_BYTES, 'CRL response exceeds size limit')
    bytes.scan(/-----BEGIN X509 CRL-----.*?-----END X509 CRL-----/m).map { |pem| OpenSSL::X509::CRL.new(pem) }
  end

  def number(crl)
    extension = crl.extensions.find { |e| e.oid == 'crlNumber' }
    require_valid(extension, 'CRL number is required')
    value = OpenSSL::ASN1.decode(OpenSSL::ASN1.decode(extension.to_der).value.last.value).value.to_i
    require_valid(value >= 0, 'Invalid CRL number')
    value
  end

  def validate(crl, cert, previous = nil, online: false)
    now = Time.now
    require_valid(crl.issuer.to_der == cert.subject.to_der && crl.signature_algorithm == 'ML-DSA-44' && crl.verify(cert.public_key), 'CRL issuer or signature mismatch')
    require_valid(crl.last_update && crl.next_update && crl.last_update <= now && now < crl.next_update, 'CRL is expired or not yet valid')
    require_valid(crl.next_update <= cert.not_after, 'CRL outlives its issuer')
    require_valid(!online || crl.next_update - crl.last_update <= ONLINE_TTL + 60, 'Online CRL validity exceeds 72 hours; rebuild the CA with crl-ttl support')
    record = record(crl)
    if previous
      require_valid(record['number'] >= previous.fetch('number'), 'CRL rollback rejected')
      require_valid(record['last_update'] >= previous.fetch('last_update'), 'CRL timestamp rollback rejected')
      require_valid(record['number'] != previous['number'] || record['sha256'] == previous['sha256'], 'Conflicting CRLs share a number')
    end
    record
  end

  def record(crl)
    {'number' => number(crl), 'sha256' => Digest::SHA256.hexdigest(crl.to_der), 'last_update' => crl.last_update.to_i}
  end

  def bundle(bytes, authorities, history)
    crls = parse_crls(bytes)
    require_valid(crls.length == authorities.length, 'Incomplete or excessive CRL bundle')
    records = {}
    ordered = authorities.map do |cert|
      matches = crls.select { |crl| crl.issuer.to_der == cert.subject.to_der && crl.verify(cert.public_key) }
      require_valid(matches.length == 1, 'Missing or duplicate issuer CRL')
      id = Digest::SHA256.hexdigest(cert.public_key.public_to_der)
      records[id] = validate(matches.first, cert, history[id], online: cert.issuer.to_der != cert.subject.to_der)
      matches.first
    end
    [ordered, records]
  end

  def atomic(path, bytes)
    directory = File.dirname(path)
    FileUtils.mkdir_p(directory, mode: 0o755)
    Tempfile.create('.crl-', directory) do |file|
      file.write(bytes)
      file.flush
      file.fsync
      file.chmod(0o644)
      File.rename(file.path, path)
      File.open(directory, File::RDONLY) { |parent| parent.fsync }
    end
  end

  def request(url, method: 'GET', data: nil, root: nil, crls: nil, cert: nil, key: nil, token: nil, credentials: false, content_type: 'application/json')
    uri = URI(url)
    require_valid(%w[http https].include?(uri.scheme) && uri.host && !uri.userinfo && !uri.fragment, 'Invalid CRL endpoint')
    require_valid(!(token || cert || credentials) || uri.scheme == 'https' && root && crls, 'Credentials require verified TLS and CRLs')
    http = Net::HTTP.new(uri.host, uri.port, nil)
    http.open_timeout = http.read_timeout = 10
    if uri.scheme == 'https'
      http.use_ssl = true
      http.min_version = OpenSSL::SSL::TLS1_3_VERSION
      http.verify_mode = OpenSSL::SSL::VERIFY_PEER
      http.verify_hostname = true
      if root
        store = OpenSSL::X509::Store.new
        store.add_cert(root)
        if crls
          crls.each { |crl| store.add_crl(crl) }
          store.flags = OpenSSL::X509::V_FLAG_CRL_CHECK | OpenSSL::X509::V_FLAG_CRL_CHECK_ALL
        end
        http.cert_store = store
      end
      if cert
        chain = certificates(cert)
        http.cert = chain.shift
        http.extra_chain_cert = chain
        http.key = OpenSSL::PKey.read(File.read(key))
      end
    end
    req = Net::HTTPGenericRequest.new(method, !data.nil?, true, uri.request_uri)
    req['X-Vault-Token'] = token if token
    req['Content-Type'] = content_type if data
    req.body = data if data
    output = +''
    http.request(req) do |response|
      require_valid(%w[200 204].include?(response.code), "CRL endpoint returned HTTP #{response.code}")
      response.read_body do |chunk|
        output << chunk
        require_valid(output.bytesize <= MAX_BYTES, 'CRL response exceeds size limit')
      end
    end
    output
  end

  def read_json(path)
    File.exist?(path) ? JSON.parse(File.read(path)) : {}
  end

  def check(path)
    status = read_json(path)
    now = Time.now.to_i
    require_valid(status['ok'] && status['last_success'].is_a?(Integer) && now - status['last_success'] <= 900 && status['last_success'] <= now, 'CRL refresh is failing or overdue')
    require_valid(status.fetch('next_expiry', 0) > now + WARNING, 'CRL or issuer expires within 24 hours; check CA renewal and Vault automatic CRL rebuilding')
  end

  def download(url, pinned, cache, revoked: false)
    # A compromised issuer must not block its revocation by returning malformed data.
    return [File.read(cache), true] if revoked && File.exist?(cache)
    [request(url, root: pinned), false]
  rescue StandardError
    # Cached issuer evidence still allows a fresh root revocation to reach clients.
    [File.read(cache), true]
  end

  def issuing_crl(bytes, issuing, pinned, root_crl)
    current = parse_crls(bytes)
    old_root = current.find { |crl| crl.issuer.to_der == pinned.subject.to_der && crl.verify(pinned.public_key) }
    validate(root_crl, pinned, record(old_root)) if old_root
    matches = current.select { |crl| crl.issuer.to_der == issuing.subject.to_der && crl.verify(issuing.public_key) }
    require_valid(matches.length == 1, 'Expected one signed issuer CRL')
    [matches.first, old_root]
  end

  def sync(config, history)
    pinned = root(config.fetch('root'))
    issuing = issuer(config.fetch('issuer'), pinned)
    roots, = bundle(request(config.fetch('root_crl_url'), root: pinned), [pinned], history)
    revoked = roots.first.revoked.any? { |entry| entry.serial == issuing.serial }
    bytes, unavailable = download(config.fetch('source_url'), pinned, config.fetch('output_file'), revoked: revoked)
    issuing_list, = issuing_crl(bytes, issuing, pinned, roots.first)
    crls, records = bundle(issuing_list.to_pem + roots.first.to_pem, [issuing, pinned], history)
    # Persist the rollback barrier before exposing a newer CRL, including after a crash.
    atomic(config.fetch('state_file'), JSON.generate(history.merge(records)))
    atomic(config.fetch('output_file'), crls.map(&:to_pem).join)
    require_valid(!unavailable, 'Issuer unavailable; validated cached issuer and current root installed, retry required')
    [records, crls.map(&:next_update).min.to_i]
  end

  def login(config, pinned, crls)
    role_id = File.read(config.fetch('role_id_file')).strip
    secret_id = File.read(config.fetch('secret_id_file')).strip
    require_valid(!role_id.empty? && !secret_id.empty?, 'Missing publisher AppRole credentials')
    response = request("#{config.fetch('vault_url').delete_suffix('/')}/v1/auth/approle/login",
                       method: 'POST', data: JSON.generate({'role_id' => role_id, 'secret_id' => secret_id}),
                       root: pinned, crls: crls, credentials: true)
    token = JSON.parse(response).fetch('auth').fetch('client_token')
    require_valid(token.is_a?(String) && !token.empty?, 'Missing publisher login token')
    token
  end

  def publish_files(config, openvox_crls, vault_crls, history)
    output = config.fetch('output_dir')
    atomic(config.fetch('state_file'), JSON.generate(history))
    atomic("#{output}/openvox-crls.pem", openvox_crls.map(&:to_pem).join)
    atomic("#{output}/vault.crl.pem", vault_crls.first.to_pem)
    atomic("#{config.fetch('trust_dir')}/outbound-crls.pem", vault_crls.map(&:to_pem).join)
    atomic("#{config.fetch('trust_dir')}/root.crl", openvox_crls.last.to_pem)
  end

  def publish(config, history)
    pinned = root(config.fetch('root'))
    openvox = issuer(config.fetch('openvox_issuer'), pinned)
    vault = issuer(config.fetch('vault_issuer'), pinned)
    roots, root_records = bundle(request(config.fetch('root_crl_url'), root: pinned), [pinned], history)
    root_crl = roots.first
    openvox_url = config.fetch('openvox_url').delete_suffix('/')
    vault_url = config.fetch('vault_url').delete_suffix('/')
    openvox_bytes, openvox_unavailable = download("#{openvox_url}/puppet-ca/v1/certificate_revocation_list/ca",
                                                pinned, "#{config.fetch('output_dir')}/openvox-crls.pem",
                                                revoked: root_crl.revoked.any? { |entry| entry.serial == openvox.serial })
    openvox_list, old_root = issuing_crl(openvox_bytes, openvox, pinned, root_crl)
    openvox_crls, openvox_records = bundle(openvox_list.to_pem + root_crl.to_pem, [openvox, pinned], history)
    vault_bytes, vault_unavailable = download("#{vault_url}/v1/pki_int/crl/pem", pinned,
                                            "#{config.fetch('output_dir')}/vault.crl.pem",
                                            revoked: root_crl.revoked.any? { |entry| entry.serial == vault.serial })
    vault_crls, vault_records = bundle(vault_bytes + root_crl.to_pem, [vault, pinned], history)
    records = root_records.merge(openvox_records).merge(vault_records)
    # Publish revocation evidence before credentials, even if it revokes an online issuer.
    publish_files(config, openvox_crls, vault_crls, history.merge(records))
    token = login(config, pinned, vault_crls)
    [openvox_crls.first, root_crl].zip(%w[openvox root]).each do |crl, name|
      request("#{vault_url}/v1/auth/cert/crls/#{name}", method: 'POST',
              data: JSON.generate({'crl' => crl.to_pem}), root: pinned, crls: vault_crls, token: token)
    end
    # Vault must learn a revoked OpenVox issuer even when that issuer is unreachable.
    require_valid(!openvox_unavailable && !vault_unavailable, 'Issuer unavailable; validated caches and root synchronized to Vault, retry required')
    unless old_root && old_root.to_der == root_crl.to_der
      request("#{openvox_url}/puppet-ca/v1/certificate_revocation_list", method: 'PUT',
              data: root_crl.to_pem, content_type: 'text/plain', root: pinned, crls: openvox_crls,
              cert: config.fetch('client_cert'), key: config.fetch('client_key'))
    end
    [records, (openvox_crls + vault_crls).map(&:next_update).min.to_i]
  end

  def run(mode, config)
    state = config.fetch('state_file')
    status = config.fetch('status_file')
    FileUtils.mkdir_p(File.dirname(state), mode: 0o755)
    File.open("#{state}.lock", File::RDWR | File::CREAT, 0o600) do |lock|
      require_valid(lock.flock(File::LOCK_EX | File::LOCK_NB), 'Another CRL refresh is running')
      begin
        history = read_json(state)
        records, expiry = mode == 'publish' ? publish(config, history) : sync(config, history)
        atomic(state, JSON.generate(history.merge(records)))
        atomic(status, JSON.generate({'ok' => true, 'last_success' => Time.now.to_i, 'next_expiry' => expiry}))
        check(status)
      rescue StandardError
        previous = read_json(status)
        atomic(status, JSON.generate(previous.merge('ok' => false)))
        raise
      end
    end
  end
end

if $PROGRAM_NAME == __FILE__
  begin
    mode, path = ARGV
    OpenVoxCRL.require_valid(ARGV.length == 2 && %w[publish sync check].include?(mode), 'Usage: crl-refresh.rb publish|sync CONFIG.json | check STATUS.json')
    if mode == 'check'
      OpenVoxCRL.check(path)
    else
      OpenVoxCRL.run(mode, JSON.parse(File.read(path)))
    end
    puts 'CRL refresh healthy'
  rescue StandardError => error
    warn(error.is_a?(OpenVoxCRL::Invalid) ? error.message : "CRL refresh failed (#{error.class}); check local inputs and endpoint availability")
    exit 1
  end
end
