class profile::common::systemd (
  Hash $units = {},
) {
  $units.each |String $unit_name, Hash $config| {
    systemd::unit_file { $unit_name:
      * => $config,
    }
  }
}
