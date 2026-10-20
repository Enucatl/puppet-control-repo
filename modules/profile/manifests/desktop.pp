class profile::desktop {
  file { '/home/user/Documents':
    ensure  => directory,
    owner   => 'user',
    group   => 'user',
    mode    => '0750',
    require => File['/home/user'],
    before  => [Posix_acl['/home/user/Documents'], Nfs::Server::Export['/home/user/Documents']],
  }
}
