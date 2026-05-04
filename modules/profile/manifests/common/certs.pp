class profile::common::certs (
  Hash $vault_certs = {},
  Hash $vault_certs_defaults = {},
  String $vault_certs_default_location = '/opt/certs',
) {
  require vault_secrets::vault_cert

  $vault_certs.each |String $subdomain, Optional[Hash] $config| {
    $default_value = "${subdomain}.${facts['networking']['fqdn']}"
    $paths = {
      'cert_chain_file' => "${vault_certs_default_location}/${subdomain}_fullchain.pem",
      'key_file'        => "${vault_certs_default_location}/${subdomain}_key.pem",
      'cert_data'       => {
        'common_name' => $default_value,
        'alt_names'   => $default_value,
      },
    }

    $vault_cert_config = deep_merge($vault_certs_defaults, $paths, $config)

    vault_cert { $subdomain:
      * => $vault_cert_config,
    }
  }
}
