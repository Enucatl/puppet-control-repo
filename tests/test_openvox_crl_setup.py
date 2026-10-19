"""Verify opt-in Vault CRL setup without credentials, networking or a daemon."""

from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUBY = (
    "/opt/puppetlabs/puppet/bin/ruby"
    if Path("/opt/puppetlabs/puppet/bin/ruby").is_file()
    else shutil.which("ruby")
)
FIXTURE = r"""
require 'tmpdir'
require ARGV.shift
scenario = ARGV.shift
calls = []
OpenVoxCRL.define_singleton_method(:root) { |_path| :pinned }
OpenVoxCRL.define_singleton_method(:issuer) do |_path, root|
  raise 'Root missing' unless root == :pinned
  :issuer
end
OpenVoxCRL.define_singleton_method(:bundle) do |bytes, certs, history|
  raise 'Trust input missing' unless bytes == 'initial-crls' && certs == [:issuer, :pinned] && history == {}
  raise OpenVoxCRL::Invalid, 'Invalid trust' if scenario == 'invalid_trust'
  [[:validated_crl], {}]
end
OpenVoxCRL.define_singleton_method(:request) do |url, **options|
  raise 'Admin TLS trust missing' unless options[:root] == :pinned && options[:crls] == [:validated_crl]
  raise 'Admin credential missing' unless options[:token] == 'fixture-admin'
  body = options[:data] && JSON.parse(options[:data])
  calls << [url, options[:method], body]
  response = case url
             when %r{/sys/auth\z}
               scenario == 'wrong_auth' ? {'data' => {'approle/' => {'type' => 'userpass'}}} : {'data' => {}}
             when %r{/role-id\z} then {'data' => {'role_id' => 'fixture-role'}}
             when %r{/secret-id\z} then {'data' => {'secret_id' => 'fixture-secret'}}
             else {}
             end
  JSON.generate(response)
end
Dir.mktmpdir do |dir|
  File.write("#{dir}/admin", 'fixture-admin')
  File.write("#{dir}/outbound-crls.pem", 'initial-crls')
  config = {
    'vault_url' => 'https://vault.example', 'root_crl_url' => 'https://vault.example/v1/pki/crl/pem',
    'root' => 'root', 'vault_issuer' => 'issuer', 'trust_dir' => dir,
    'role_id_file' => "#{dir}/role-id", 'secret_id_file' => "#{dir}/secret-id",
  }
  if %w[idempotent wrong_role partial_pair].include?(scenario)
    File.write(config['role_id_file'], scenario == 'wrong_role' ? 'other-role' : 'fixture-role')
    File.write(config['secret_id_file'], 'existing-secret') unless scenario == 'partial_pair'
  end
  config['root_crl_url'] = 'https://other.example/v1/pki/crl/pem' if scenario == 'foreign_root'
  expected_failure = %w[wrong_auth wrong_role partial_pair invalid_trust foreign_root].include?(scenario)
  begin
    OpenVoxCRLSetup.configure(config, "#{dir}/admin")
    raise 'Unsafe setup accepted' if expected_failure
  rescue OpenVoxCRL::Invalid
    raise unless expected_failure
  end
  if %w[partial_pair invalid_trust foreign_root].include?(scenario)
    raise 'Mutation before preflight verification' unless calls.empty?
  elsif !expected_failure
    configurations = calls.select { |call| call[0].end_with?('/config/crl') }
    raise 'Wrong CRL mounts' unless configurations.map(&:first) == [
      'https://vault.example/v1/pki/config/crl', 'https://vault.example/v1/pki_int/config/crl'
    ]
    raise 'Wrong CRL schedule' unless configurations.all? do |_, method, body|
      method == 'POST' && body == {'expiry' => '72h', 'auto_rebuild' => true,
        'auto_rebuild_grace_period' => '71h55m', 'enable_delta' => false, 'disable' => false}
    end
    role = calls.find { |call| call[0].end_with?('/role/openvox-crl-publisher') }[2]
    raise 'Wrong machine authentication' unless role['secret_id_ttl'] == 0 && role['secret_id_num_uses'] == 0 &&
      role['token_type'] == 'batch' && role['token_ttl'] == '10m' && role['token_max_ttl'] == '10m' &&
      role['bind_secret_id'] && role['token_no_default_policy'] && role['token_policies'] == ['openvox-crl-publisher']
    policy = calls.find { |call| call[0].end_with?('/sys/policies/acl/openvox-crl-publisher') }[2]['policy']
    raise 'Excess publisher permissions' unless policy.scan(/path "([^"]+)"/).flatten.sort == %w[auth/cert/crls/openvox auth/cert/crls/root]
    secret_calls = calls.select { |call| call[0].end_with?('/secret-id') }
    if scenario == 'idempotent'
      raise 'Existing SecretID replaced' unless secret_calls.empty? && File.read(config['secret_id_file']) == 'existing-secret'
    else
      raise 'Missing machine credentials' unless secret_calls.length == 1 && File.read(config['role_id_file']).strip == 'fixture-role' &&
        File.read(config['secret_id_file']).strip == 'fixture-secret'
      raise 'Credentials not private' unless [config['role_id_file'], config['secret_id_file']].all? { |path| File.stat(path).mode & 0777 == 0600 }
    end
    raise 'Publisher configured to rotate CRLs itself' if calls.any? { |call| call[0].include?('/crl/rotate') }
  end
end
puts "PASS #{scenario}"
"""


@pytest.mark.parametrize(
    "scenario",
    [
        "new",
        "idempotent",
        "partial_pair",
        "invalid_trust",
        "foreign_root",
        "wrong_auth",
        "wrong_role",
    ],
)
def test_crl_setup(scenario: str) -> None:
    """Check trust preflight, least privilege, machine login and credential reuse."""
    if RUBY is None:
        pytest.skip("Ruby is unavailable")
    result = subprocess.run(
        [
            RUBY,
            "-e",
            FIXTURE,
            str(ROOT / "modules/profile/files/openvox/configure-crl.rb"),
            scenario,
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == f"PASS {scenario}"
    assert "fixture-secret" not in result.stderr + result.stdout
