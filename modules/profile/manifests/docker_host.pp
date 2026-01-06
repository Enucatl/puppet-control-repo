class profile::docker_host (
  # We use lookup() defaults here so your existing Hiera data works without changes.
  Hash    $vault_certs                  = lookup('vault_certs', Hash, 'deep', {}),
  Hash    $vault_certs_defaults         = lookup('vault_certs_defaults', Hash, 'deep', {}),
  String  $vault_certs_default_location = lookup('vault_certs_default_location', String, 'first', '/opt/certs'),
  String  $printer_smb_password         = lookup('printer::smbpasswd'),
  String  $pictures_smb_password        = lookup('pictures::smbpasswd'),
  Hash    $sysctl_settings              = lookup('sysctl_hash', Hash, 'deep', {}),
) {

  # ------------------------------------------------------------------
  # 1. Vault Certificate Management
  # ------------------------------------------------------------------
  $vault_certs.each |String $subdomain, Optional[Hash] $config| {
    # Default Common Name calculation
    $default_value = "${subdomain}.${trusted['certname']}"
    
    # path defaults based on the location variable
    $paths = {
      cert_chain_file => "${vault_certs_default_location}/${subdomain}_fullchain.pem",
      key_file        => "${vault_certs_default_location}/${subdomain}_key.pem",
      cert_data       => {
        common_name => $default_value,
        alt_names   => $default_value,
      }
    }
    
    # Merge: Global Defaults -> Calculated Paths -> Specific Cert Config
    $vault_cert_config = deep_merge($vault_certs_defaults + $paths, $config)
    
    vault_cert { $subdomain:
      * => $vault_cert_config,
    }
  }

  # Allow Protonmail Bridge container to read the docker key
  # We subscribe to Vault_cert['docker'] which is created by the iterator above
  posix_acl { "${vault_certs_default_location}/docker_key.pem":
    action     => set,
    permission => [
      "user:101001:r--",
    ],
    subscribe  => Vault_cert['docker'],
  }

  # Trigger Traefik reload when certs change
  exec { "trigger_traefik_reload":
    command     => '/usr/bin/touch /opt/docker/traefik/data/config.yml',
    refreshonly => true,
    subscribe   => Vault_cert['docker'],
  }

  # ------------------------------------------------------------------
  # 2. Samba Configuration
  # ------------------------------------------------------------------
  # Ensure the base samba class is applied before adding users
  require ::samba

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
  
  # Ensure Service is running (The 'docker' class usually handles this, 
  # but site.pp had explicit declaration, so preserving it here)
  service { 'docker':
    ensure => running,
    enable => true,
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

  # ------------------------------------------------------------------
  # 4. Sysctl
  # ------------------------------------------------------------------
  create_resources(sysctl, $sysctl_settings)
}
