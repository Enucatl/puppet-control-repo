# Ubuntu's user namespace restriction requires an explicit application profile.
class profile::bubblewrap {
  if $facts['os']['name'] == 'Ubuntu' and versioncmp($facts['os']['release']['full'], '24.04') >= 0 {
    package { 'apparmor':
      ensure => installed,
    }

    file { '/etc/apparmor.d/bwrap':
      ensure  => file,
      owner   => 'root',
      group   => 'root',
      mode    => '0644',
      source  => 'puppet:///modules/profile/bwrap.apparmor',
      require => Package['apparmor'],
      notify  => Exec['load-bwrap-apparmor'],
    }

    exec { 'load-bwrap-apparmor':
      command     => '/sbin/apparmor_parser --replace /etc/apparmor.d/bwrap',
      refreshonly => true,
    }
  }
}
