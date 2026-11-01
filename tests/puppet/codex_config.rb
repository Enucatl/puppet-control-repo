# Run only in a disposable container: this exercises real system and user paths.
abort 'Requires CODEX_CONFIG_TEST_CONTAINER=1' unless ENV['CODEX_CONFIG_TEST_CONTAINER'] == '1'
require 'fileutils'
require 'open3'
require 'tmpdir'

root = File.expand_path('../..', __dir__)
puppet = '/opt/puppetlabs/bin/puppet'
code = 'include profile::codex_config'
Dir.mktmpdir do |work|
  run = lambda do
    output, status = Open3.capture2e(puppet, 'apply', '--modulepath', "#{root}/modules",
                                    '--confdir', work, '--vardir', "#{work}/cache",
                                    '--execute', code)
    abort output unless status.success?
  end
  FileUtils.mkdir_p('/root/.codex')
  config = '/root/.codex/config.toml'
  run.call
  raise 'User config created' if File.exist?(config) || File.symlink?(config)
  system_config = '/etc/codex/config.toml'
  raise 'System defaults differ' unless File.binread(system_config) == File.binread("#{root}/modules/profile/files/codex-config.toml")
  stat = File.stat(system_config)
  raise 'System defaults permissions differ' unless stat.uid.zero? && stat.gid.zero? && stat.mode & 0777 == 0644

  state = "model = \"local-choice\"\n[projects.\"/tmp/local\"]\ntrust_level = \"trusted\"\n"
  File.write(config, state)
  2.times { run.call }
  raise 'User state overwritten' unless File.read(config) == state

  File.unlink(config)
  File.write('/root/other-config.toml', state)
  File.symlink('/root/other-config.toml', config)
  run.call
  raise 'Unrelated symlink changed' unless File.readlink(config) == '/root/other-config.toml'
  raise 'Unrelated config changed' unless File.read(config) == state
end

require 'yaml'
%w[desktop docker].each do |role|
  data = YAML.safe_load(File.read("#{root}/data/roles/#{role}.yaml"))
  raise "Missing Codex defaults for #{role}" unless data.fetch('classes').include?('profile::codex_config')
end

puts 'Codex defaults, role enablement and state preservation passed'
