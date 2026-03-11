class profile::docker_host (
  Hash $git_deploy_projects = {},
) {

  require profile::common

  $git_deploy_projects.each |String $project, Hash $params| {
    profile::docker_deploy { $project:
      * => $params,
    }
  }

  # Ensure Service is running (The 'docker' class usually handles this,
  # but site.pp had explicit declaration, so preserving it here)
  service { 'docker':
    ensure => running,
    enable => true,
  }

  # Alloy user management
  user { 'alloy':
    ensure  => present,
    groups  => 'docker',
    require => Package['alloy'],
    notify  => Service['alloy'],
  }

}
