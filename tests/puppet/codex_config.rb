# Run only in a disposable container: this exercises real system and user paths.
abort 'Requires CODEX_CONFIG_TEST_CONTAINER=1' unless ENV['CODEX_CONFIG_TEST_CONTAINER'] == '1'
require 'fileutils'
require 'open3'
require 'tmpdir'

root = File.expand_path('../..', __dir__)
puppet = '/opt/puppetlabs/bin/puppet'
code = <<~PUPPET
  define vcsrepo() {}
  vcsrepo { '/root/.vim': }
  exec { ['dotfiles-rake-links', 'sync-codex-plugins-user']: command => '/bin/true' }
  class { 'profile::codex_config': username => 'root' }
PUPPET
Dir.mktmpdir do |work|
  run = lambda do
    output, status = Open3.capture2e(puppet, 'apply', '--modulepath', "#{root}/modules",
                                    '--confdir', work, '--vardir', "#{work}/cache",
                                    '--execute', code)
    abort output unless status.success?
  end
  FileUtils.mkdir_p('/root/.codex')
  config = '/root/.codex/config.toml'
  target = '/root/.vim/dotfiles/codex/config.toml'
  FileUtils.mkdir_p(File.dirname(target))
  File.write(target, "old dotfiles config\n")
  File.symlink(target, config)
  run.call
  raise "Live obsolete symlink survived" if File.symlink?(config)
  raise "Old target changed" unless File.read(target) == "old dotfiles config\n"
  File.unlink(target)
  # The obsolete target may already be gone when this catalog arrives.
  File.symlink(target, config)
  run.call
  raise 'Obsolete symlink survived' if File.symlink?(config)
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

# Compile with explicit stubs to check ordering without external Forge modules.
require 'puppet'
Dir.mktmpdir do |work|
  Puppet.initialize_settings(['--confdir', work, '--vardir', "#{work}/cache", '--code', code])
  environment = Puppet::Node::Environment.create(:testing, ["#{root}/modules"])
  catalog = Puppet::Parser::Compiler.compile(Puppet::Node.new('fixture', environment: environment))
  graph = catalog.to_ral.relationship_graph
  raise 'Dependency cycle' if graph.report_cycles_in_graph
  edges = graph.edges.map { |edge| [edge.source.ref, edge.target.ref] }
  before = Array(catalog.resource('Exec', 'remove-dotfiles-codex-config-link')[:before]).map(&:to_s)
  raise 'Missing ordering before dotfiles repo' unless before.include?('Vcsrepo[/root/.vim]')
  ['Exec[dotfiles-rake-links]', 'Exec[sync-codex-plugins-user]'].each do |target|
    raise "Missing ordering before #{target}" unless edges.include?(['Exec[remove-dotfiles-codex-config-link]', target])
  end
end


require 'yaml'
%w[desktop docker].each do |role|
  data = YAML.safe_load(File.read("#{root}/data/roles/#{role}.yaml"))
  raise "Missing Codex defaults for #{role}" unless data.fetch('classes').include?('profile::codex_config')
end

puts 'Codex defaults, role enablement, cutover, state preservation and ordering passed'
