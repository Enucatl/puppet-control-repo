class profile::common::systemd (
  Hash $units         = {},
  Hash $user_services = {},
) {
  if $trusted['certname'] in ['atlas.home.arpa', 'aperture.home.arpa', 'cerebrum.home.arpa'] {
    file { '/usr/local/libexec':
      ensure => directory,
      owner  => 'root',
      group  => 'root',
      mode   => '0755',
    }
    file { '/usr/local/libexec/openvox-child-policy.rb':
      ensure => file,
      source => 'puppet:///modules/profile/openvox-child-policy.rb',
      owner  => 'root',
      group  => 'root',
      mode   => '0644',
    }
  }

  $units.each |String $unit_name, Hash $config| {
    systemd::unit_file { $unit_name:
      * => $config,
    }
  }

  $user_services.each |String $service_name, Hash $config| {
    systemd::user_service { $service_name:
      * => $config,
    }

    Systemd::Unit_file[$config['unit']] ~> Systemd::User_service[$service_name]
  }
}
