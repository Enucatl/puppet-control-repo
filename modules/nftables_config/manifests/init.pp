# Class: nftables_config
#
# This class manages nftables configuration files for specific rules.
# It ensures the Docker-related accept rules are persistent and that
# the main nftables configuration includes all files from /etc/nftables/.
#
class nftables_config {

  # Ensure nftables package is installed
  package { 'nftables':
    ensure => present,
  }

  # Ensure the /etc/nftables/ directory exists for included rules
  file { '/etc/nftables':
    ensure  => directory,
    owner   => 'root',
    group   => 'root',
    mode    => '0755', # Standard directory permissions
    require => Package['nftables'],
  }

  # Manage the main nftables configuration file to include other rule files
  file { '/etc/nftables.conf':
    ensure  => present,
    # Use an EPP template to add the 'include' directive
    content => epp('nftables_config/nftables.conf.epp'),
    owner   => 'root',
    group   => 'root',
    mode    => '0644',
    require => Package['nftables'],
    notify  => Service['nftables'], # Restart nftables service on change
  }

  # Manage the nftables rule file for DOCKER-USER chain
  # This file will be included by the main /etc/nftables.conf due to the EPP template
  file { '/etc/nftables/docker_user_rules.nft':
    ensure  => present,
    source  => 'puppet:///modules/nftables_config/docker_user_rules.nft',
    owner   => 'root',
    group   => 'root',
    mode    => '0644',
    require => File['/etc/nftables'], # Ensure directory exists first
    notify  => Service['nftables'],    # Restart nftables service on change
  }

  # Ensure nftables service is running and enabled
  service { 'nftables':
    ensure    => running,
    enable    => true,
    subscribe => [
      File['/etc/nftables/docker_user_rules.nft'], # Subscribe to custom rules file
      File['/etc/nftables.conf'],                # Subscribe to main config file
    ],
  }
}
