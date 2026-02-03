class profile::docker_host (
  String  $printer_smb_password  = lookup('printer::smbpasswd'),
  String  $pictures_smb_password = lookup('pictures::smbpasswd'),
) {

  require docker
  require profile::common
  require ::samba
  require posix_acl::requirements

  # Trigger Traefik reload when certs change
  exec { "trigger_traefik_reload":
    command     => '/usr/bin/touch /opt/docker/traefik/data/config.yml',
    refreshonly => true,
    subscribe   => Vault_cert['docker'],
  }

  # ------------------------------------------------------------------
  # 2. Samba Configuration
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
  # 3. Docker & Infrastructure Fixes
  # ------------------------------------------------------------------
  
  # Allow Protonmail Bridge container to read the docker key
  # We subscribe to Vault_cert['docker'] which is created by the iterator above
  posix_acl { "${profile::common::vault_certs_default_location}/docker_key.pem":
    action     => set,
    permission => [
      "user:101001:r--",
    ],
    subscribe  => Vault_cert['docker'],
  }

  # Fix permissions for Loki volume so mapped users can write logs
  file { '/var/lib/docker/100000.100000/volumes/grafana-loki_loki_data/_data':
    ensure    => 'directory',
    owner     => '110001', # 100000 (remap base) + 10001 (loki uid)
    group     => '110001',
    mode      => '0751',
    subscribe => Service['docker'],
    recurse   => false,
  }

  # Alloy user management
  user { 'alloy':
    ensure  => present,
    groups  => 'docker',
    require => Package['alloy'], 
    notify  => Service['alloy'], 
  }

}
