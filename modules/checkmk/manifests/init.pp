# Class: checkmk
#
# This class manages the configuration files related to Checkmk, a monitoring solution.
# It ensures the presence of specific configuration files for Checkmk, including Docker configurations.
#
# Parameters:
#   None
#
# Example Usage:
#   class { 'checkmk': }
#
class checkmk {

  # file - Manage the Checkmk Docker configuration file.
  #
  # Parameters:
  #   - path: The path to the Checkmk Docker configuration file.
  #   - ensure: Specifies the desired state of the file (default: 'present').
  #   - source: The source URL for the configuration file.
  #
  file { '/etc/check_mk/docker.cfg':
    ensure => 'present',
    source => 'puppet:///modules/checkmk/docker.cfg',
  }

  # file - Manage the Checkmk Docker plugin file.
  #
  # Parameters:
  #   - path: The path to the Checkmk Docker plugin file.
  #   - ensure: Specifies the desired state of the file (default: 'present').
  #   - source: The source URL for the plugin file.
  #
  file { '/usr/lib/check_mk_agent/plugins/mk_docker.py':
    ensure => 'present',
    source => 'puppet:///modules/checkmk/mk_docker.py',
  }

}
