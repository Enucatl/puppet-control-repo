#!/opt/puppetlabs/puppet/bin/ruby

require 'digest'
require 'json'
require 'openssl'
require 'rbconfig'
require 'time'

check = 'read_runtime_identity'
begin
  ssl = '/etc/puppetlabs/puppet/ssl'
  ca = '/etc/puppetlabs/puppetserver/ca'
  data = '/opt/puppetlabs/server/data/puppetserver/server_data'
  server_key_path = "#{ssl}/private_keys/openvox.rehearsal.test.pem"
  ca_key_path = "#{ca}/ca_key.pem"
  bootstrap_key_path = "#{data}/bootstrap/intermediate.key"
  certificates = ->(path) do
    File.read(path).scan(/-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----/m).map do |pem|
      OpenSSL::X509::Certificate.new(pem)
    end
  end
  root = OpenSSL::X509::Certificate.new(File.read('/trust/root.pem'))
  intermediate, chain_root, *extra = certificates.call('/trust/openvox-chain.pem')
  leaf, *served_chain = certificates.call("#{ssl}/certs/openvox.rehearsal.test.pem")
  imported_ca = certificates.call("#{ca}/ca_crt.pem").first
  server_key = OpenSSL::PKey.read(File.read(server_key_path))
  ca_key = OpenSSL::PKey.read(File.read(ca_key_path))

  check = 'seed_only_keys_and_certificate_algorithms'
  args = [server_key_path, ca_key_path]
  args << bootstrap_key_path if File.exist?(bootstrap_key_path)
  raise check unless system(RbConfig.ruby, File.join(__dir__, 'verify-crypto.rb'),
                           *args, '--cert', '/trust/root.pem', '--cert', '/trust/openvox-chain.pem',
                           '--cert', "#{ssl}/certs/openvox.rehearsal.test.pem",
                           out: File::NULL, err: File::NULL)

  check = 'complete_matching_root_intermediate_and_listener_chain'
  raise check unless extra.empty? && chain_root.to_der == root.to_der &&
                     served_chain.map(&:to_der) == [intermediate.to_der, root.to_der] &&
                     imported_ca.to_der == intermediate.to_der

  check = 'chain_signatures_and_key_pairs'
  raise check unless root.issuer.to_der == root.subject.to_der && root.verify(root.public_key) &&
                     intermediate.issuer.to_der == root.subject.to_der && intermediate.verify(root.public_key) &&
                     leaf.issuer.to_der == intermediate.subject.to_der && leaf.verify(intermediate.public_key) &&
                     server_key.public_to_der == leaf.public_key.public_to_der &&
                     ca_key.public_to_der == intermediate.public_key.public_to_der &&
                     ca_key.public_to_der != root.public_key.public_to_der &&
                     server_key.public_to_der != root.public_key.public_to_der
  if File.exist?(bootstrap_key_path)
    raise check unless OpenSSL::PKey.read(File.read(bootstrap_key_path)).public_to_der == ca_key.public_to_der
  end

  check = 'authority_constraints'
  [[root, 1], [intermediate, 0]].each do |cert, path_length|
    constraints = cert.extensions.find { |extension| extension.oid == 'basicConstraints' }
    usage = cert.extensions.find { |extension| extension.oid == 'keyUsage' }
    raise check unless constraints&.critical? && constraints.value == "CA:TRUE, pathlen:#{path_length}" &&
                       usage&.critical? && usage.value.include?('Certificate Sign') && usage.value.include?('CRL Sign')
  end
  raise check if leaf.extensions.any? { |extension| extension.oid == 'basicConstraints' && extension.value.include?('CA:TRUE') }

  check = 'validity_and_issuer_lifetime_caps'
  now = Time.now
  raise check unless [root, intermediate, leaf].all? { |cert| cert.not_before <= now && now < cert.not_after } &&
                     leaf.not_after <= intermediate.not_after && intermediate.not_after <= root.not_after

  check = 'crl_chain_signatures_issuers_and_expiry'
  crls = File.read("#{ca}/ca_crl.pem").scan(/-----BEGIN X509 CRL-----.*?-----END X509 CRL-----/m).map do |pem|
    OpenSSL::X509::CRL.new(pem)
  end
  raise check unless crls.length == 2 && [intermediate, root].all? do |issuer|
    matching = crls.select { |crl| crl.issuer.to_der == issuer.subject.to_der }
    matching.length == 1 && matching.first.signature_algorithm == 'ML-DSA-44' &&
      matching.first.verify(issuer.public_key) && matching.first.last_update <= now &&
      matching.first.next_update && now < matching.first.next_update
  end

  check = 'no_offline_root_private_key_in_runtime_mounts'
  runtime_paths = [ssl, ca, data, '/opt/puppetlabs/server/data/puppetserver/jruby-gems',
                   '/etc/puppetlabs/code', '/git', '/var/cache/r10k', '/trust']
  found_keys = []
  unparsed_keys = []
  runtime_paths.each do |directory|
    Dir.glob("#{directory}/**/*", File::FNM_DOTMATCH).each do |path|
      next unless File.file?(path)

      File.binread(path).scan(/(^-----BEGIN ((?:[A-Z]+ )*PRIVATE KEY)-----\r?\n.*?^-----END \2-----\r?$)/mn).each do |pem, _label|
        begin
          private_key = OpenSSL::PKey.read(pem, '')
        rescue OpenSSL::OpenSSLError, ArgumentError
          # Modules contain dummy/encrypted PEM fixtures; report unparsed blocks.
          unparsed_keys << path
          next
        end
        found_keys << path
        raise check if private_key.public_to_der == root.public_key.public_to_der
      end
    end
  end

  puts JSON.generate(ok: true, scope: 'OpenVox runtime files; excludes Vault storage',
                     root_sha256: Digest::SHA256.hexdigest(root.to_der),
                     intermediate_sha256: Digest::SHA256.hexdigest(intermediate.to_der),
                     server_sha256: Digest::SHA256.hexdigest(leaf.to_der),
                     server_public_key_sha256: Digest::SHA256.hexdigest(server_key.public_to_der),
                     intermediate_public_key_sha256: Digest::SHA256.hexdigest(ca_key.public_to_der),
                     root_expires: root.not_after.utc.iso8601,
                     intermediate_expires: intermediate.not_after.utc.iso8601,
                     server_expires: leaf.not_after.utc.iso8601,
                     crls: crls.map { |crl| { issuer: crl.issuer.to_s, next_update: crl.next_update.utc.iso8601 } },
                     root_key_check: 'Parsed PEM keys compared against root public key; encrypted/unparseable blocks excluded',
                     private_key_files: found_keys.uniq.sort,
                     unparsed_private_key_files: unparsed_keys.uniq.sort)
rescue StandardError
  puts JSON.generate(ok: false, failed_check: check)
  exit 1
end
