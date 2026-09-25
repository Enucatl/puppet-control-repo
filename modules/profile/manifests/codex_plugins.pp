class profile::codex_plugins {
  package { 'jq':
    ensure => installed,
  }

  file { '/usr/local/sbin/puppet-codex-plugins-sync':
    ensure => file,
    owner  => 'root',
    group  => 'root',
    mode   => '0755',
    source => 'puppet:///modules/profile/codex-plugins-sync',
  }

  exec { 'sync-codex-plugins-user':
    command     => '/usr/local/sbin/puppet-codex-plugins-sync',
    user        => 'user',
    path        => ['/usr/local/sbin', '/usr/local/bin', '/usr/bin', '/bin'],
    environment => ['HOME=/home/user', 'USER=user', 'LOGNAME=user'],
    timeout     => 300,
    onlyif      => 'id user',
    require     => [
      File['/usr/local/sbin/puppet-codex-plugins-sync'],
      Package['jq'],
      Exec['sync-user-toolchain-user'],
      Exec['dotfiles-rake-links'],
    ],
  }
}
