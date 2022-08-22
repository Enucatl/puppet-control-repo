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
hiera_include('classes')

node default {
  # This is where you can declare classes for all nodes.
  # Example:
  #   class { 'my_class': }
  include dns::client
}

node 'dns.home.arpa' {

  $dns_variables = {
    'cpe_id' => Sensitive(Deferred('vault_key', [
      'https://vault.home.arpa:8200/v1/secret/dns',
      'cert',
      'cpe-id',
      ]))
  }

  dnsmasq::conf { 'local-dns':
    ensure  => present,
    content => Deferred('inline_epp', [file('dns/dnsmasq.conf.epp'), $dns_variables]),
    require => Class['ca']
  }
}

node 'nuc10i7fnh.home.arpa' {
  include dns::client

  include vault_secrets::vault_cert

  vault_cert { 'traefik-dashboard':
    ensure            => present,
    vault_uri         => 'https://vault.home.arpa:8200/v1/pki_int/issue/home-dot-arpa',
    auth_path         => 'cert',
    cert_data         => {
      'common_name' => "traefik-dashboard.${trusted['certname']}", 
      'ttl'         =>  '48h',  # 2 day
    },
    renewal_threshold => 2,
    ca_chain_file     => '/home/user/chain.pem',
    cert_file     => '/home/user/cert.pem',
    key_file     => '/home/user/key.pem',
  }

}
