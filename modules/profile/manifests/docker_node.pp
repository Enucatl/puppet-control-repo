class profile::docker_node (
  String $printer_smb_password  = lookup('profile::docker_host::printer_smb_password'),
  String $pictures_smb_password = lookup('profile::docker_host::pictures_smb_password'),
) {

  require profile::common
  require ::samba

  file { '/etc/systemd/system/networkd-dispatcher.service.d/networkd-dispatcher-ignore-veth.conf':
    ensure => absent,
    notify => Exec['systemctl-daemon-reload-networkd-dispatcher-cleanup'],
  }

  exec { 'systemctl-daemon-reload-networkd-dispatcher-cleanup':
    command     => '/usr/bin/systemctl daemon-reload',
    refreshonly => true,
  }

  # Trigger Traefik reload when certs change
  exec { 'trigger_traefik_reload':
    command     => '/usr/bin/touch /opt/docker/traefik/data/config.yml',
    refreshonly => true,
    subscribe   => Vault_cert['docker'],
  }

  # ------------------------------------------------------------------
  # Samba Configuration
  # ------------------------------------------------------------------

  # Create 'printer' samba user
  exec { 'create_samba_user_printer':
    path    => ['/bin', '/usr/bin'],
    command => "printf '${printer_smb_password}\\n${printer_smb_password}\\n' | smbpasswd -a -s printer",
    unless  => "pdbedit -L -u printer",
  }

  # Create 'pictures' samba user
  exec { 'create_samba_user_pictures':
    path    => ['/bin', '/usr/bin'],
    command => "printf '${pictures_smb_password}\\n${pictures_smb_password}\\n' | smbpasswd -a -s pictures",
    unless  => "pdbedit -L -u pictures",
  }

  # Paperless consumption directory
  file { '/opt/paperless-consume':
    ensure => directory,
    owner  => 'printer',
    group  => 'printer',
    mode   => '0777',
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
