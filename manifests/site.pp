## site.pp ##

# This file (./manifests/site.pp) is the main entry point
# used when an agent connects to a master and asks for an updated configuration.
# https://puppet.com/docs/puppet/latest/dirs_manifest.html
#
# Global objects like filebuckets and resource defaults should go in this file,
# as should the default node definition if you want to use it.

## Active Configurations ##

# Disable filebucket by default for all File resources:
# https://github.com/puppetlabs/docs-archive/blob/master/pe/2015.3/release_notes.markdown#filebucket-resource-no-longer-created-by-default
File { backup => false }

## Node Definitions ##

# The default node definition matches any node lacking a more specific node
# definition. If there are no other node definitions in this file, classes
# and resources declared in the default node definition will be included in
# every node's catalog.
#
# Note that node definitions in this file are merged with node data from the
# Puppet Enterprise console and External Node Classifiers (ENC's).
#
# For more on node definitions, see: https://puppet.com/docs/puppet/latest/lang_node_definitions.html
$classes = lookup('classes', Array[String])

node default {
  $classes.include

}

node 'vault.home.arpa' {
  $classes.include

  # https://www.vaultproject.io/docs/configuration#disable_mlock
  systemd::dropin_file { 'vault.conf':
    unit    => 'vault.service',
    content => "[Service]\nLimitMEMLOCK=infinity",
  }
}

node 'dns.home.arpa' {
  delete($classes, 'dns::client').include

  dnsmasq::conf { 'local-dns':
    ensure  => present,
    content => stdlib::deferrable_epp(
      'dns/dnsmasq.conf.epp',
      {'cpe_id' => lookup('dns::cpe-id')}
    ),
    require => Class['ca']
  }

}

node 'nuc10i7fnh.home.arpa' {
  $classes.include

  $vault_certs = lookup('vault_certs')
  $vault_certs_defaults = lookup('vault_certs_defaults')
  $vault_certs_default_location = lookup('vault_certs_default_location')
  $vault_certs.each |String $subdomain, Optional[Hash] $config| {
      $paths = {
        'cert_chain_file' => "${vault_certs_default_location}/${subdomain}.${trusted['certname']}/fullchain.pem",
        'key_file'        => "${vault_certs_default_location}/${subdomain}.${trusted['certname']}/privkey.pem",
      }
      $merged_config = deep_merge($vault_certs_defaults + $paths, $config)
      notify { "notify_${subdomain}":
        message => "${merged_config}"
      }
      vault_cert { "example":
        'ensure' => present,
        'vault_uri' => 'https://vault.home.arpa:8200/v1/pki_int/issue/home-dot-arpa,'
        'auth_path' => 'cert',
        'cert_data' => {'ttl' => '2160h'},
        'renewal_threshold' => 5,
        'cert_chain_owner' => 'user,'
        'cert_chain_group' => 'user,'
        'key_owner' => 'user,'
        'key_group' => 'user,'
        'cert_chain_file' => '/home/user/docker/traefik/data/certs/traefik.nuc10i7fnh.home.arpa/fullchain.pem,'
        'key_file' => '/home/user/docker/traefik/data/certs/traefik.nuc10i7fnh.home.arpa/privkey.pem'
      }
      vault_cert { $subdomain:
        * => $merged_config,
      }
  }

  postfix::hash { '/etc/postfix/sasl_passwd':
    content => "${lookup('postfix::relayhost')} ${lookup('smtp_sasl_username')}:${lookup('smtp_sasl_password')}",
    require => Class['postfix'],
  }

  file { '/var/lib/docker':
    ensure => 'directory',
    mode   => '0711',
  }

}
