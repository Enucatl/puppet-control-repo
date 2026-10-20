class profile::docker_host (
  Array[String]       $stack_directories            = [],
  Hash                $git_deploy_projects         = {},
  Boolean             $scheduled_refresh           = false,
  String              $scheduled_refresh_calendar   = 'Sun *-*-01..07 04:00:00',
) {

  include profile::common

  $stack_directories.each |String $stack| {
    file { "/opt/docker/${stack}":
      ensure  => directory,
      owner   => 'user',
      group   => 'docker',
      mode    => '2770',
      require => Class['freeipa::client'],
    }
  }

  $git_deploy_projects.each |String $project, Hash $params| {
    $project_defaults = {
      'scheduled_refresh' => $scheduled_refresh,
      'refresh_calendar'  => $scheduled_refresh_calendar,
    }

    profile::docker_deploy { $project:
      require => Class['profile::common'],
      * => $project_defaults + $params,
    }
  }

  # Package installation remains in the Docker role's Hiera data.
  service { 'docker':
    ensure => running,
    enable => true,
  }

  contain profile::docker::wolf_network
}
