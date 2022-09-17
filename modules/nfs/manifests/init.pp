class nfs {
  service { 'rpc-svcgssd':
    ensure => 'running',
  }

  file { '/etc/default/nfs-common':
    ensure => 'present',
    source => 'puppet:///modules/nfs/nfs-common',
    notify => Service['rpc-svcgssd'],
  }
}
