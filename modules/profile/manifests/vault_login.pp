class profile::vault_login {
  file { '/usr/local/bin/vault-pam-login.sh':
    ensure  => file,
    owner   => 'root',
    group   => 'root',
    mode    => '0755',
    content => epp('profile/vault-pam-login.sh.epp'),
  }

  file { '/usr/share/pam-configs/vault-login':
    ensure  => file,
    owner   => 'root',
    group   => 'root',
    mode    => '0644',
    content => epp('profile/vault-pam-login-pam-config.epp'),
    notify  => Exec['pam-auth-update-vault'],
  }

  exec { 'pam-auth-update-vault':
    command     => '/usr/sbin/pam-auth-update --package',
    refreshonly => true,
  }
}
