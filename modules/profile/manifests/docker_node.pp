class profile::docker_node (
  Sensitive[String] $printer_smb_password  = Sensitive(lookup('profile::docker_node::printer_smb_password', String)),
  Sensitive[String] $pictures_smb_password = Sensitive(lookup('profile::docker_node::pictures_smb_password', String)),
) {

  require profile::common
  include ::samba

  # Trigger Traefik reload when certs change
  exec { 'trigger_traefik_reload':
    command     => '/usr/bin/touch /opt/docker/traefik/data/config.yml',
    refreshonly => true,
    subscribe   => Vault_cert['docker'],
  }

  # ------------------------------------------------------------------
  # Samba Configuration
  # ------------------------------------------------------------------

  class { 'profile::paperless_scans':
    printer_smb_password => $printer_smb_password,
  }

  # Create 'pictures' samba user
  exec { 'create_samba_user_pictures':
    command => Sensitive("/usr/bin/printf '%s\\n%s\\n' ${stdlib::shell_escape($pictures_smb_password.unwrap)} ${stdlib::shell_escape($pictures_smb_password.unwrap)} | /usr/bin/smbpasswd -a -s pictures"),
    unless  => '/usr/bin/pdbedit -L -u pictures',
    require => Class['samba::install'],
  }

  # ------------------------------------------------------------------
  # Container-specific infrastructure
  # ------------------------------------------------------------------

  # Fix permissions for Loki volume so mapped users can write logs
  file { '/var/lib/docker/100000.100000/volumes/grafana-loki_loki_data/_data':
    ensure    => 'directory',
    owner     => '110001', # 100000 (remap base) + 10001 (loki uid)
    group     => '110001',
    mode      => '0751',
    subscribe => Service['docker'],
    recurse   => false,
  }

}
