class profile::alloy (
  String  $loki_url      = 'https://loki.docker.home.arpa/loki/api/v1/push',
  Boolean $enable_docker = false,
) {

  # Generate the configuration string from the template
  $config_content = epp('profile/alloy.config.epp', {
    'loki_url'      => $loki_url,
    'enable_docker' => $enable_docker,
  })

  # Pass the generated string to the official module
  class { 'grafana_alloy':
    config => $config_content,
  }
}
