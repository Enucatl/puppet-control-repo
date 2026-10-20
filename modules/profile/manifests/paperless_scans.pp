class profile::paperless_scans (
  Sensitive[String] $printer_smb_password = lookup('profile::docker_node::printer_smb_password', Sensitive[String]),
) {
  include ::samba
  require freeipa::client

  exec { 'create_samba_user_printer':
    command => Sensitive("/usr/bin/printf '%s\\n%s\\n' ${stdlib::shell_escape($printer_smb_password.unwrap)} ${stdlib::shell_escape($printer_smb_password.unwrap)} | /usr/bin/smbpasswd -a -s printer"),
    unless  => '/usr/bin/pdbedit -L -u printer',
    require => Class['samba::install'],
  }

  file { '/opt/paperless-consume':
    ensure => directory,
    owner  => 'printer',
    group  => 'printer',
    mode   => '0777',
  }

  samba::share { 'scans':
    comment        => 'Printer Uploads',
    path           => '/opt/paperless-consume',
    browseable     => true,
    writable       => true,
    valid_users    => ['printer'],
    force_user     => 'printer',
    create_mask    => '0666',
    directory_mask => '0777',
  }

}
