# The bootstrap installs this host's delegated keytab outside Puppet catalogs.
class freeipa_users::provision (
  Hash $users = {},
) {
  if $trusted['authenticated'] != 'remote' or $trusted['certname'] != 'docker.home.arpa' {
    fail('FreeIPA provisioning requires the authenticated docker.home.arpa certificate')
  }

  file { '/etc/puppet-ipa-provisioner.keytab':
    ensure => file,
    owner  => 'root',
    group  => 'root',
    mode   => '0400',
  }

  file { '/usr/local/sbin/puppet-ipa-provision-user':
    ensure => file,
    source => 'puppet:///modules/freeipa_users/provision-user.sh',
    owner  => 'root',
    group  => 'root',
    mode   => '0700',
  }

  $users.each |$username, $attrs| {
    $keytab = $attrs.get('keytab', undef)
    $args = [$username, $attrs['first'], $attrs['last'], $attrs.get('shell', '/usr/sbin/nologin')]
    $command_args = $keytab ? {
      undef   => $args,
      default => $args + [$keytab],
    }
    $escaped_args = $command_args.map |$arg| { stdlib::shell_escape($arg) }.join(' ')

    exec { "ipa-user-provision-${username}":
      command => "/usr/local/sbin/puppet-ipa-provision-user ${escaped_args}",
      unless  => "/usr/local/sbin/puppet-ipa-provision-user --check ${escaped_args}",
      require => File['/usr/local/sbin/puppet-ipa-provision-user', '/etc/puppet-ipa-provisioner.keytab'],
    }

    if $keytab {
      file { $keytab:
        ensure  => file,
        owner   => 'root',
        group   => 'root',
        mode    => '0400',
        require => Exec["ipa-user-provision-${username}"],
      }
    }
  }
}
