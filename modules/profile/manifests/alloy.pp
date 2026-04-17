class profile::alloy (
  String            $loki_url            = 'https://loki.docker.home.arpa/loki/api/v1/push',
  Boolean           $enable_docker       = false,
  String            $extra_config        = '',
  Optional[Integer]  $maxmind_account_id  = lookup('profile::docker_host::maxmind_account_id', Optional[Integer], 'first', undef),
  Optional[String]   $maxmind_license_key = lookup('profile::docker_host::maxmind_license_key', Optional[String], 'first', undef),
  Optional[String]   $local_ipv6_prefix   = lookup('ipv6-prefix', Optional[String], 'first', undef),
) {

  $local_ipv6_geoip_skip_prefix = $local_ipv6_prefix ? {
    undef   => 'no-local-ipv6-prefix-configured',
    default => $local_ipv6_prefix,
  }
  $rendered_extra_config = regsubst(
    $extra_config,
    '__LOCAL_IPV6_PREFIX__',
    $local_ipv6_geoip_skip_prefix,
    'G',
  )

  $effective_maxmind_account_id = $maxmind_account_id ? {
    undef   => undef,
    default => String($maxmind_account_id),
  }

  $effective_extra_config = if $effective_maxmind_account_id != undef and $maxmind_license_key != undef {
    $rendered_extra_config
  } else {
    ''
  }

  # Generate the configuration string from the template
  $config_content = epp('profile/alloy.config.epp', {
    'loki_url'      => $loki_url,
    'enable_docker' => $enable_docker,
    'extra_config'  => $effective_extra_config,
  })

  # Pass the generated string to the official module
  class { 'grafana_alloy':
    config => $config_content,
  }
}
