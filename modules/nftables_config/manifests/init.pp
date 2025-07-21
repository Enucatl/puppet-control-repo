# Class: nftables_config
#
# This class manages nftables configuration files for specific rules.
# It ensures the Docker-related accept rules are persistent and that
# the main nftables configuration includes all files from /etc/nftables/.
# It also ensures nftables starts before Docker.
#
class nftables_config {

  # Ensure nftables package is installed
  package { 'docker':
    ensure => present,
  }

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
  systemd::dropin_file { 'docker_after_nftables.conf':
    unit    => 'docker.service',
    content => "[Unit]\nRequires=nftables.service\nAfter=nftables.service\n",
    # Notify systemd to reload its configuration when this file changes.
    notify  => Exec['systemd-daemon-reload'],
    # This drop-in is only useful if the docker package is installed.
    require => Package['docker'],
  }

  # Now, manage the services. Puppet will handle the dependencies correctly.
  # nftables will be started first.
  service { 'nftables':
    ensure => running,
    enable => true,
    require => Package['nftables'],
  }

  # Thanks to the drop-in file, systemd now knows to start docker after nftables.
  service { 'docker':
    ensure  => running,
    enable  => true,
    require => Package['docker'],
    # Explicitly subscribe to the drop-in to ensure the service is restarted
    # with the new configuration if the drop-in file changes.
    subscribe => Systemd::Dropin_file['docker_after_nftables.conf'],
  }
}
