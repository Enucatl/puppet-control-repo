# Class: puppet_configuration
#
# This class manages the configuration settings for the Puppet agent by applying
# specified settings to the puppet.conf file.
#
# Parameters:
#   - $settings: A hash containing Puppet configuration settings.
#
# Example Usage:
# puppet_configuration::settings:
#   autosign:
#     section: server
#     setting: autosign
#     value: /etc/puppetlabs/code/environments/production/scripts/autosign.py
#   external_node_classifier_node_terminus:
#     section: server
#     setting: node_terminus
#     value: exec
#   external_node_classifier_external_nodes:
#     section: server
#     setting: external_nodes
#     value: /etc/puppetlabs/code/environments/production/scripts/external_node_classifier.py
#
class puppet_configuration (
  Hash $settings,
) {
  # Determine the configuration directory based on the operating system family.
  $conf_dir = $facts['os']['family'] ? {
    'windows' => "${facts['common_appdata']}/PuppetLabs/puppet/etc",
    default   => '/etc/puppetlabs/puppet',
  }

  # Set default values, including the path to the puppet.conf file.
  $defaults = {
    path => "${conf_dir}/puppet.conf",
  }

  # Create resources using the ini_setting type to manage puppet.conf settings.
  create_resources(ini_setting, $settings, $defaults)
}
