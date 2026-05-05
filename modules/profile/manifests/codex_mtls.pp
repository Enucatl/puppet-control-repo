class profile::codex_mtls (
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

  $mtls_dir = "${resolved_home}/.config/home-arpa/mtls"

  file { [
    "${resolved_home}/.config",
    "${resolved_home}/.config/home-arpa",
    $mtls_dir,
  ]:
    ensure => directory,
    owner  => $username,
    group  => $username,
    mode   => '0700',
  }
}
