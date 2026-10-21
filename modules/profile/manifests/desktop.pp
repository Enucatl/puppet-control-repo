class profile::desktop {
  # Remove the old fstab owner without accessing an offline automount.
  augeas { 'cognition-native-automount':
    context => '/files/etc/fstab',
    changes => "rm *[file = '/export/cognition']",
    before  => Systemd::Unit_file['export-cognition.mount'],
    notify  => Systemd::Daemon_reload['export-cognition.mount'],
  }

  file { '/home/user/Documents':
    ensure  => directory,
    owner   => 'user',
    group   => 'user',
    mode    => '0750',
    require => File['/home/user'],
    before  => [Posix_acl['/home/user/Documents'], Nfs::Server::Export['/home/user/Documents']],
  }
}
