class profile::common (
  Hash    $vault_certs                  = {},
  Hash    $vault_certs_defaults         = {},
  String  $vault_certs_default_location = '/opt/certs',
  Hash    $sysctl_settings              = {},
  Hash    $cronjobs                     = {},
  Hash    $services                     = {},
  Hash    $systemd_units                = {},
  Hash    $posix_acls                   = {},
  Hash    $ssh_authorized_keys          = {},
) {

  require vault_secrets::vault_cert

  # 1. Vault Certificate Management
  $vault_certs.each |String $subdomain, Optional[Hash] $config| {
    # Default Common Name calculation
    $default_value = "${subdomain}.${trusted['certname']}"
    
    # Path defaults based on the location variable
    $paths = {
      'cert_chain_file' => "${vault_certs_default_location}/${subdomain}_fullchain.pem",
      'key_file'        => "${vault_certs_default_location}/${subdomain}_key.pem",
      'cert_data'       => {
        'common_name' => $default_value,
        'alt_names'   => [$default_value], # Note: alt_names usually expects an array
      }
    }
    
    # Merge: Global Defaults -> Calculated Paths -> Specific Cert Config
    # We use deep_merge (from stdlib) to ensure nested cert_data merges correctly
    $vault_cert_config = deep_merge($vault_certs_defaults, $paths, $config)
    
    vault_cert { $subdomain:
      * => $vault_cert_config,
    }
  }

  # 4. Sysctl
  # Ensure the hash isn't empty before calling create_resources
  if !empty($sysctl_settings) {
    create_resources(sysctl, $sysctl_settings)
  }

  if !empty($cronjobs) {
    resources { 'cron': purge => true }
    create_resources(cron, $cronjobs)
  }

  if !empty($services) {
    create_resources(service, $services)
  }

  if !empty($ssh_authorized_keys) {
    create_resources(ssh_authorized_key, $ssh_authorized_keys)
  }

  $systemd_units.each |String $unit_name, Hash $config| {
    systemd::unit_file { $unit_name:
      * => $config,
    }
  }

  # Enable SSSD Kerberos ticket renewal so NFS krb5p mounts stay accessible
  # beyond the default 24h TGT lifetime. FreeIPA already issues 7-day renewable
  # tickets; this tells SSSD to actually renew them every hour.
  # The Sssd augeas lens stores section content as children of 'target' nodes,
  # where the target node value is the section name. Select the right section
  # with the predicate target[.='domain/home.arpa'].
  augeas { 'sssd_krb5_renewal':
    context => '/files/etc/sssd/sssd.conf',
    changes => [
      "set target[.='domain/home.arpa']/krb5_renewable_lifetime 7d",
      "set target[.='domain/home.arpa']/krb5_renew_interval 1h",
    ],
    require => Class['freeipa::install::client'],
    notify  => Service['sssd'],
  }

  # Create POSIX ACLs from hash
  require posix_acl::requirements
  $posix_acls.each |String $path, Hash $config| {
    posix_acl { $path:
      * => $config,
    }
  }
}
