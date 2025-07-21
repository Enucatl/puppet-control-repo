# Class: nftables_config
#
# This class manages nftables configuration files for specific rules.
# It ensures the Docker-related accept rules are persistent and that
# the main nftables configuration includes all files from /etc/nftables/.
# It also ensures nftables starts after Docker to avoid chain not found errors.
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
  exec { 'systemctl daemon-reload for nftables':
    command     => '/bin/systemctl daemon-reload',
    refreshonly => true, # Only run when notified by the drop-in file
    # Ensure this runs before the nftables service is managed
    before      => Service['nftables'],
  }

  # Create a systemd drop-in file for nftables.service
  # This adds 'After=docker.service' and 'Wants=docker.service' to the nftables service unit.
  # This ensures nftables starts only after Docker has initialized its firewall chains.
  systemd::dropin_file { 'nftables_after_docker.conf':
    unit    => 'nftables.service',
    # We use Wants=docker.service to pull it in as a dependency, and After=docker.service
    # to ensure nftables starts after docker is ready.
    content => "[Unit]\nAfter=docker.service\nWants=docker.service\n",
    # When this file changes, notify the daemon-reload exec
    notify  => Exec['systemctl daemon-reload for nftables'],
    require => Package['nftables'], # Drop-in only makes sense if nftables is installed
  }

  # Ensure nftables service is running and enabled
  service { 'nftables':
    ensure    => running,
    enable    => true,
    # The subscription ensures it restarts if config files change.
    # The 'before' on the 'exec' resource ensures daemon-reload happens first if the drop-in changes.
    subscribe => [
      File['/etc/nftables/docker_user_rules.nft'],
      File['/etc/nftables.conf'],
    ],
  }
}
