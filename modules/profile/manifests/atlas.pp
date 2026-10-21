class profile::atlas {
  require profile::common

  Class['freeipa::client'] -> Class['freeipa_users::provision']

  exec { 'trigger_traefik_reload':
    command     => '/usr/bin/touch /opt/docker/traefik/data/config.yml',
    refreshonly => true,
    subscribe   => Vault_cert['atlas'],
  }
}
