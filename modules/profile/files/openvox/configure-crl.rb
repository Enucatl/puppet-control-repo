#!/opt/puppetlabs/puppet/bin/ruby
# One-time authenticated setup; the scheduled publisher never gets these privileges.
require_relative 'crl-refresh'

module OpenVoxCRLSetup
  ROLE = 'openvox-crl-publisher'
  module_function

  def write_secret(path, value)
    Tempfile.create('.credential-', File.dirname(path)) do |file|
      file.chmod(0o600)
      file.write(value + "\n")
      file.flush
      file.fsync
      File.rename(file.path, path)
    end
  end

  def configure(config, admin_token_file)
    vault_url = config.fetch('vault_url').delete_suffix('/')
    root_path = config.fetch('root_crl_url').delete_prefix("#{vault_url}/v1/")
    OpenVoxCRL.require_valid(root_path.match?(%r{\A[a-zA-Z0-9_/-]+/crl/pem\z}),
                            'Configure the root CRL on its own Vault when root_crl_url has a different origin')
    root_mount = root_path.delete_suffix('/crl/pem')
    role_file = config.fetch('role_id_file')
    secret_file = config.fetch('secret_id_file')
    existing = [File.exist?(role_file), File.exist?(secret_file)]
    OpenVoxCRL.require_valid(existing.uniq.length == 1, 'Incomplete AppRole credential pair; restore it before setup')
    OpenVoxCRL.require_valid(File.directory?(File.dirname(role_file)) && File.directory?(File.dirname(secret_file)),
                            'Create the protected credentials directory before setup')
    if existing.first
      OpenVoxCRL.require_valid(!File.read(role_file).strip.empty? && !File.read(secret_file).strip.empty?, 'Empty AppRole credentials')
    end

    pinned = OpenVoxCRL.root(config.fetch('root'))
    issuer = OpenVoxCRL.issuer(config.fetch('vault_issuer'), pinned)
    crls, = OpenVoxCRL.bundle(File.read("#{config.fetch('trust_dir')}/outbound-crls.pem"), [issuer, pinned], {})
    token = File.read(admin_token_file).strip
    OpenVoxCRL.require_valid(!token.empty?, 'Empty administrator token')
    api = lambda do |method, path, data = nil|
      bytes = OpenVoxCRL.request("#{vault_url}/v1/#{path}", method: method,
                                data: data && JSON.generate(data), root: pinned, crls: crls, token: token)
      bytes.empty? ? {} : JSON.parse(bytes)
    end

    # Vault owns CRL signing and its periodic rebuild, even while this job is down.
    [root_mount, 'pki_int'].uniq.each do |mount|
      api.call('POST', "#{mount}/config/crl", {
        'expiry' => '72h', 'auto_rebuild' => true,
        'auto_rebuild_grace_period' => '71h55m', 'enable_delta' => false, 'disable' => false,
      })
    end
    mounts = api.call('GET', 'sys/auth').fetch('data')
    if mounts.key?('approle/')
      OpenVoxCRL.require_valid(mounts.fetch('approle/').fetch('type') == 'approle', 'Existing auth/approle is not AppRole')
    else
      api.call('POST', 'sys/auth/approle', {'type' => 'approle'})
    end
    api.call('PUT', "sys/policies/acl/#{ROLE}", {'policy' => File.read(File.join(__dir__, 'crl-publisher-policy.hcl'))})
    api.call('POST', "auth/approle/role/#{ROLE}", {
      'bind_secret_id' => true, 'secret_id_ttl' => 0, 'secret_id_num_uses' => 0,
      'token_policies' => [ROLE], 'token_no_default_policy' => true,
      'token_type' => 'batch', 'token_ttl' => '10m', 'token_max_ttl' => '10m', 'token_num_uses' => 0,
    })
    role_id = api.call('GET', "auth/approle/role/#{ROLE}/role-id").fetch('data').fetch('role_id')
    if existing.first
      OpenVoxCRL.require_valid(File.read(role_file).strip == role_id, 'Installed RoleID differs; restore the established AppRole')
    else
      secret_id = api.call('POST', "auth/approle/role/#{ROLE}/secret-id", {}).fetch('data').fetch('secret_id')
      write_secret(role_file, role_id)
      write_secret(secret_file, secret_id)
    end
  end
end

if $PROGRAM_NAME == __FILE__
  begin
    OpenVoxCRL.require_valid(ARGV.length == 2, 'Usage: configure-crl.rb CONFIG.json ADMIN_TOKEN_FILE')
    OpenVoxCRLSetup.configure(JSON.parse(File.read(ARGV[0])), ARGV[1])
    puts 'CRL automation configured; AppRole credentials installed or preserved'
  rescue StandardError => error
    warn(error.is_a?(OpenVoxCRL::Invalid) ? error.message : "CRL setup failed (#{error.class}); check authenticated configuration and administrator permissions")
    exit 1
  end
end
