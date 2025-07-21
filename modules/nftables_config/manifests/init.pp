# Class: nftables_config
#
# This class manages nftables configuration files for specific rules.
# It ensures the Docker-related accept rules are persistent and that
# the main nftables configuration includes all files from /etc/nftables/.
# It also ensures nftables starts before Docker.
#
class nftables_config {

  # Ensure nftables package is installed
  package { 'nftables':
    ensure => present,
  }

  service { 'nftables':
    ensure => running,
    enable => true,
    require => Package['nftables'],
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
    content => epp('nftables_config/nftables.conf.epp'),
    owner   => 'root',
    group   => 'root',
    mode    => '0644',
    require => Package['nftables'],
    notify  => Service['nftables'], # This will trigger a restart if content changes
  }

  # Manage the nftables rule file for DOCKER-USER chain
  file { '/etc/nftables/docker_user_rules.nft':
    ensure  => present,
    source  => 'puppet:///modules/nftables_config/docker_user_rules.nft',
    owner   => 'root',
    group   => 'root',
    mode    => '0644',
    require => File['/etc/nftables'], # Ensure directory exists first
    notify  => Service['nftables'],    # This will trigger a restart if content changes
  }

  # Exec to trigger systemctl daemon-reload
  # This is crucial for systemd to pick up changes in drop-in files before service management.
  exec { 'systemd-daemon-reload':
    command     => '/bin/systemctl daemon-reload',
    refreshonly => true, # Only run when notified by the drop-in file
  }

  # Create a systemd drop-in file for the docker.service.
  # This makes Docker wait for nftables to be ready before starting.
  file { '/etc/systemd/system/docker.service.d/docker_after_nftables.conf':
    ensure => file,
    content => "[Unit]\nRequires=nftables.service\nAfter=nftables.service\n",
    require => Package['docker'],
  }

  File['/etc/systemd/system/docker.service.d/docker_after_nftables.conf'] ~> Exec['systemd-daemon-reload'] ~> Service['docker']

}
