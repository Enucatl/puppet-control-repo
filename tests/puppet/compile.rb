# Compile only; never apply resources or contact the production Hiera backend.
require 'json'
require 'puppet'

input = JSON.parse(STDIN.read)
Puppet.initialize_settings([
  '--confdir', input.fetch('workdir'),
  '--vardir', File.join(input.fetch('workdir'), 'cache'),
  '--hiera_config', input.fetch('hiera_config'),
  '--code', input.fetch('code'),
  '--node_terminus', 'plain',
  '--strict', 'warning',
])
Puppet::Util::Log.newdestination(:console)
Puppet::Util::Log.level = :err
environment = Puppet::Node::Environment.create(:testing, input.fetch('modulepath'))
facts = Puppet::Node::Facts.new(input.fetch('certname'), input.fetch('facts'))
node = Puppet::Node.new(input.fetch('certname'), environment: environment, facts: facts)
node.fact_merge(facts)
node.trusted_data = {
  'authenticated' => 'remote', 'certname' => node.name,
  'hostname' => node.name.split('.').first, 'domain' => 'home.arpa', 'extensions' => {},
}
catalog = Puppet::Parser::Compiler.compile(node)
# Validate resource references and cycles without invoking providers.
# Agents apply as root; permit catalog conversion under an unprivileged test user.
Puppet.features.define_singleton_method(:root?) { true }
graph = catalog.to_ral.relationship_graph
raise 'Catalog contains dependency cycles' if graph.report_cycles_in_graph
data = catalog.to_data_hash
data['relationships'] = graph.edges.map do |edge|
  edge.to_data_hash.merge('source' => edge.source.ref, 'target' => edge.target.ref)
end
puts JSON.generate(data)
