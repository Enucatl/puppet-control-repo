# Class: puppet_configuration
#
# This class manages the configuration settings for the Puppet agent by applying
# specified settings to the puppet.conf file.
#
# Parameters:
#   - $settings: A hash containing Puppet configuration settings.
#   - $facter_blocklist: A list of Facter facts to block.
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
  Array[String] $facter_blocklist = [],
) {
  # Determine the configuration directory based on the operating system family.
  $conf_dir = $facts['os']['family'] ? {
    'windows' => "${facts['common_appdata']}/PuppetLabs/puppet/etc",
    default   => '/etc/puppetlabs/puppet',
  }

  $facter_conf_dir = $facts['os']['family'] ? {
    'windows' => "${facts['common_appdata']}/PuppetLabs/facter/etc",
    default   => '/etc/puppetlabs/facter',
  }

  $facter_conf_dir_attributes = $facts['os']['family'] ? {
    'windows' => {},
    default   => {
      owner => 'root',
      group => 'root',
      mode  => '0755',
    },
  }

  $facter_conf_file_attributes = $facts['os']['family'] ? {
    'windows' => {},
    default   => {
      owner => 'root',
      group => 'root',
      mode  => '0644',
    },
  }

  # Set default values, including the path to the puppet.conf file.
  $defaults = {
    path => "${conf_dir}/puppet.conf",
  }

  # Create resources using the ini_setting type to manage puppet.conf settings.
  create_resources(ini_setting, $settings, $defaults)

  if !empty($facter_blocklist) {
    $quoted_facter_blocklist = $facter_blocklist.map |String $fact| { "\"${fact}\"" }

    file { $facter_conf_dir:
      ensure => directory,
      *      => $facter_conf_dir_attributes,
    }

    file { "${facter_conf_dir}/facter.conf":
      ensure  => file,
      content => "facts : {\n  blocklist : [ ${quoted_facter_blocklist.join(', ')} ]\n}\n",
      require => File[$facter_conf_dir],
      *       => $facter_conf_file_attributes,
    }
  }
}
