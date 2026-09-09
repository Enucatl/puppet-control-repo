class profile::alloy (
  String            $loki_url            = 'https://loki.docker.home.arpa/loki/api/v1/push',
  Boolean           $enable_docker       = false,
  Boolean           $manage_docker_user  = false,
  Boolean           $manage_geoip        = false,
  Boolean           $enable_router_enrichment = false,
  String            $router_listen_address = '10.0.0.128',
  Optional[String]   $maxmind_account_id  = lookup('profile::alloy::maxmind_account_id', Optional[String], 'first', undef),
  Optional[String]   $maxmind_license_key = lookup('profile::alloy::maxmind_license_key', Optional[String], 'first', undef),
  Optional[String]   $local_ipv6_prefix   = lookup('ipv6-prefix', Optional[String], 'first', undef),
) {
  $geoip_credentials_available = $maxmind_account_id != undef and $maxmind_license_key != undef

  # These resources used to belong to docker_host, after profile::common.
  # Keep their scope independent of router enrichment and Docker log collection.
  if $manage_docker_user or $manage_geoip {
    include profile::common
  }

  if $manage_docker_user {
    user { 'alloy':
      ensure  => present,
      groups  => 'docker',
      require => [Class['profile::common'], Package['alloy']],
      notify  => Service['alloy'],
    }
  }

  if $manage_geoip and $geoip_credentials_available {
    class { 'profile::alloy::geoip':
      account_id  => $maxmind_account_id,
      license_key => $maxmind_license_key,
      require     => Class['profile::common'],
    }
    contain profile::alloy::geoip
  }

  $local_ipv6_geoip_skip_prefix = $local_ipv6_prefix ? {
    undef   => 'no-local-ipv6-prefix-configured',
    default => $local_ipv6_prefix,
  }
  $router_config = if $enable_router_enrichment and $geoip_credentials_available {
    epp('profile/alloy/router.config.epp', {
      'listen_address'    => $router_listen_address,
      'local_ipv6_prefix' => $local_ipv6_geoip_skip_prefix,
    })
  } else {
    ''
  }

  # Generate the configuration string from the template
  $config_content = epp('profile/alloy.config.epp', {
    'loki_url'       => $loki_url,
    'enable_docker'  => $enable_docker,
    'router_config'  => $router_config,
  })

  # Pass the generated string to the official module
  class { 'grafana_alloy':
    config => $config_content,
  }
}
