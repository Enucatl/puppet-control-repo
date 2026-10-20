class profile::scratch (
  Sensitive[String] $pictures_smb_password = lookup('profile::docker_node::pictures_smb_password', Sensitive[String]),
) {
  include ::samba

  mount { '/scratch':
    ensure  => mounted,
    device  => 'UUID=99b387e9-afd5-4e15-bcbe-b471275fbb07',
    fstype  => 'ext4',
    options => 'defaults,noatime',
    pass    => 2,
    atboot  => true,
  }

  Mount['/scratch'] -> Nfs::Server::Export['/scratch']

  exec { 'create_samba_user_pictures':
    command => Sensitive("/usr/bin/printf '%s\\n%s\\n' ${stdlib::shell_escape($pictures_smb_password.unwrap)} ${stdlib::shell_escape($pictures_smb_password.unwrap)} | /usr/bin/smbpasswd -a -s pictures"),
    unless  => '/usr/bin/pdbedit -L -u pictures',
    require => Class['samba::install'],
  }
}
