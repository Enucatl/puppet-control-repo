class profile::docker_host (
  Hash                $git_deploy_projects         = {},
  Boolean             $scheduled_refresh           = false,
  String              $scheduled_refresh_calendar   = 'Sun *-*-01..07 04:00:00',
) {

  require profile::common

  $git_deploy_projects.each |String $project, Hash $params| {
    $project_defaults = {
      'scheduled_refresh' => $scheduled_refresh,
      'refresh_calendar'  => $scheduled_refresh_calendar,
    }

    profile::docker_deploy { $project:
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
