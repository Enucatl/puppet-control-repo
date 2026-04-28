class profile::docker_deploy::health_check (
  String $script_path = '/usr/local/sbin/docker-compose-health-check',
) {
  file { $script_path:
    ensure => file,
    owner  => 'root',
    group  => 'root',
    mode   => '0755',
    source => 'puppet:///modules/profile/docker_compose_health_check.py',
  }
}
