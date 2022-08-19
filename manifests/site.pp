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

  ca_cert::ca { 'puppet_ca':
    ensure => 'trusted',
    source => 'puppet:///modules/ca/puppet-ca.crt',
  }

  ca_cert::ca { 'root_2022_ca':
    ensure => 'trusted',
    source => 'puppet:///modules/ca/root_2022_ca.crt',
  }

  file { '/etc/ssh/ssh_ca.pub':
    source => 'puppet:///modules/ca/ssh_ca.pub',
    owner  => 'root',
    group  => 'root',
    mode   => '0644',
    before => Class['ssh'],
  }

  dnsmasq::conf { 'local-dns':
    ensure  => present,
    content => "server=${facts['networking']['dhcp']}\nno-resolv",
  }


}

node 'dns.home.arpa' {

  $cpe_id = Deferred('vault_lookup::lookup', ['secret/dns', 'https://vault.home.arpa:8200'])

  dnsmasq::conf { 'local-dns':
    ensure  => present,
    content => Deferred('inline_epp', [file('dns/dnsmasq.conf.epp'), $cpe_id]),
  }
}
