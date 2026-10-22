# Authenticate with an existing user ticket, including tickets delegated by SSH.
class profile::vault_sso {
  file { '/usr/local/bin/vault-sso-login':
    ensure => file,
    owner  => 'root',
    group  => 'root',
    mode   => '0755',
    source => 'puppet:///modules/profile/vault-sso-login',
  }

  file { '/etc/profile.d/vault-sso.sh':
    ensure  => file,
    owner   => 'root',
    group   => 'root',
    mode    => '0644',
    source  => 'puppet:///modules/profile/vault-sso.sh',
    require => File['/usr/local/bin/vault-sso-login'],
  }
}
