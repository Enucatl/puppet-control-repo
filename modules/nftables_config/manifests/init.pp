# Class: nftables_config
#
# This class manages nftables configuration files for specific rules.
# It ensures the Docker-related accept rules are persistent.
#
class nftables_config {

  # Ensure nftables package is installed
  package { 'nftables':
    ensure => present,
  }

  # Manage the nftables rule file for DOCKER-USER chain
  # This file will be included by the main /etc/nftables.conf
  # (assuming default Debian nftables setup which includes *.nft from /etc/nftables/)
  file { '/etc/nftables/docker_user_rules.nft':
    ensure  => present,
    source  => 'puppet:///modules/nftables_config/docker_user_rules.nft',
    owner   => 'root',
    group   => 'root',
    mode    => '0644',
    require => Package['nftables'], # Ensure package is installed first
    notify  => Service['nftables'],  # Restart nftables service on change
  }

  # Ensure nftables service is running and enabled
  service { 'nftables':
    ensure    => running,
    enable    => true,
    subscribe => File['/etc/nftables/docker_user_rules.nft'], # Re-subscribe for idempotency
  }
}
