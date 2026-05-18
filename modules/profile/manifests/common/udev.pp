class profile::common::udev (
  Hash $rules = {},
) {
  $rules.each |String $rule_name, Hash $config| {
    systemd::udev::rule { $rule_name:
      * => $config,
    }
  }
}
