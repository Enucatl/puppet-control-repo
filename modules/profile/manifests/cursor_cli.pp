class profile::cursor_cli (
  String $username       = 'user',
  Optional[String] $home = undef,
) {
  $resolved_home = $home ? {
    undef   => $username ? {
      'root'  => '/root',
      default => "/home/${username}",
    },
    default => $home,
  }

  $os = $facts['kernel'] ? {
    'Linux'  => 'linux',
    'Darwin' => 'darwin',
    default  => fail("Unsupported Cursor CLI OS: ${facts['kernel']}"),
  }

  $arch = $facts['os']['architecture'] ? {
    'x86_64'  => 'x64',
    'amd64'   => 'x64',
    'aarch64' => 'arm64',
    'arm64'   => 'arm64',
    default   => fail("Unsupported Cursor CLI architecture: ${facts['os']['architecture']}"),
  }

  file { '/usr/local/sbin/cursor-latest-version':
    ensure => file,
    owner  => 'root',
    group  => 'root',
    mode   => '0755',
    source => 'puppet:///modules/profile/cursor-latest-version',
  }

  file { '/usr/local/sbin/cursor-install':
    ensure => file,
    owner  => 'root',
    group  => 'root',
    mode   => '0755',
    source => 'puppet:///modules/profile/cursor-install',
  }

  exec { 'update-cursor-cli':
    command => "/usr/local/sbin/cursor-install ${resolved_home} ${os} ${arch}",
    unless  => '/usr/local/sbin/cursor-latest-version --current ${resolved_home}',
    user    => $username,
    environment => [
      "HOME=${resolved_home}",
    ],
    require => [
      File['/usr/local/sbin/cursor-latest-version'],
      File['/usr/local/sbin/cursor-install'],
    ],
  }
}
