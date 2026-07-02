class profile::common::systemd (
  Hash $units         = {},
  Hash $user_services = {},
) {
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
