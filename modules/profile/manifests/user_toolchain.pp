class profile::user_toolchain (
  String $username           = 'user',
  Optional[String] $home     = undef,
  Boolean $manage_uv         = true,
  Boolean $manage_rustup     = true,
  Boolean $manage_nvm        = true,
  Boolean $npm_ignore_scripts = true,
  Integer $npm_before_days    = 7,
  String $rust_toolchain     = 'stable',
  String $node_version       = 'node',
  Array[String] $npm_globals = [],
) {
  $resolved_home = $home ? {
    undef   => $username ? {
      'root'  => '/root',
      default => "/home/${username}",
    },
    default => $home,
  }

  $required_packages = [
    'bash',
    'ca-certificates',
    'curl',
    'git',
    'util-linux',
  ]

  $required_packages.each |String $package_name| {
    if !defined(Package[$package_name]) {
      package { $package_name:
        ensure => installed,
      }
    }
  }

  file { '/usr/local/sbin/puppet-user-toolchain-sync':
    ensure  => file,
    owner   => 'root',
    group   => 'root',
    mode    => '0755',
    content => epp('profile/user-toolchain-sync.sh.epp', {
      'home'           => $resolved_home,
      'manage_uv'      => $manage_uv,
      'manage_rustup'  => $manage_rustup,
      'manage_nvm'     => $manage_nvm,
      'npm_ignore_scripts' => $npm_ignore_scripts,
      'npm_before_days'    => $npm_before_days,
      'npm_globals'    => $npm_globals,
      'rust_toolchain' => $rust_toolchain,
      'username'       => $username,
      'node_version'   => $node_version,
    }),
  }

  exec { "sync-user-toolchain-${username}":
    command     => '/usr/local/sbin/puppet-user-toolchain-sync',
    user        => $username,
    path        => ['/usr/local/sbin', '/usr/local/bin', '/usr/bin', '/bin'],
    environment => [
      "HOME=${resolved_home}",
      "USER=${username}",
      "LOGNAME=${username}",
    ],
    timeout     => 1800,
    onlyif      => "id ${username}",
    require     => [
      File['/usr/local/sbin/puppet-user-toolchain-sync'],
      Package[$required_packages],
    ],
  }
}
