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

node 'dns.home.arpa' {
  $classes.include

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

  create_resources(vault_cert, lookup('vault_certs'))

  postfix::hash { '/etc/postfix/sasl_passwd':
    content => "${lookup('postfix::relayhost')} ${lookup('smtp_sasl_username')}:${lookup('smtp_sasl_password')}",
    require => Class['postfix'],
  }

}
