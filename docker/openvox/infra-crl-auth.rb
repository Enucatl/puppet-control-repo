# Add one machine-specific permission while retaining the package's default rules.
require 'hocon/config_factory'
require 'hocon/config_value_factory'

path = ARGV.fetch(0)
config = Hocon::ConfigFactory.parse_file(path)
rule = Hocon::ConfigFactory.parse_string(<<~'HOCON').root
  match-request: {
    path: "^/puppet-ca/v1/certificate_revocation_list$"
    type: regex
    method: put
  }
  # Matching stops at this rule; retain the package's existing administrator grant.
  allow: [${OPENVOX_CRL_PUBLISHER_CERTNAME}, {extensions: {pp_cli_auth: "true"}}]
  sort-order: 499
  name: "OpenVox CRL publisher"
HOCON
rules = config.get_list('authorization.rules').to_array.reject do |existing|
  existing.to_config.get_string('name') == 'OpenVox CRL publisher'
end
config = config.with_value('authorization.rules', Hocon::ConfigValueFactory.from_any_ref([rule] + rules))
File.write(path, config.root.render)
