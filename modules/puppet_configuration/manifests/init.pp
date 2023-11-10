class puppet_configuration (
  Hash $settings,
) {

  $conf_dir = $facts['os']['family'] ? {
    'windows' => "${facts['common_appdata']}/PuppetLabs/puppet/etc",
    default => '/etc/puppetlabs/puppet'
  }
  $defaults = {
    path =>  "${conf_dir}/puppet.conf"
  }
  create_resources(ini_setting, $settings, $defaults)
}
