# Retained temporarily to remove the obsolete checker from existing hosts.
class profile::docker_deploy::health_check (
  String $script_path = '/usr/local/sbin/docker-compose-health-check',
) {
  file { $script_path:
    ensure => absent,
  }
}
