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

node 'pihole.home.arpa' {
  delete($classes, 'dns::client').include

  # forward resolution of *.nuc10i7fnh.home.arpa subdomains to traefik
  file { '/etc/dnsmasq.d/10-nuc10i7fnh':
    ensure  => present,
    content => 'address=/nuc10i7fnh.home.arpa/192.168.2.28',
  }

  file { '/etc/network/interfaces':
    ensure  => present,
    content => stdlib::deferrable_epp(
      'dns/interfaces.epp',
      {'ipv4' => lookup('dns::client::server_ipv4')}
    ),
    notify  =>  Service['networking'],
  }

  service { 'networking':
    ensure => running,
  }

}

node 'nuc10i7fnh.home.arpa' {
  $classes.include

  $vault_certs = lookup('vault_certs')
  $vault_certs_defaults = lookup('vault_certs_defaults')
  $vault_certs_default_location = lookup('vault_certs_default_location')
  $vault_certs.each |String $subdomain, Optional[Hash] $config| {
    $domain = $config['domain']
    if $domain == undef {
      $domain = "${subdomain}.${trusted['certname']}"
    }
    $alt_names = $config['alt_names']
    if $alt_names == undef {
      $alt_names = "${subdomain}.${trusted['certname']}"
    }
    $common_name = $config['common_name']
    if $common_name == undef {
      $common_name = "${subdomain}.${trusted['certname']}"
    }
    $paths = {
      cert_chain_file => "${vault_certs_default_location}/${domain}/fullchain.pem",
      key_file        => "${vault_certs_default_location}/${domain}/privkey.pem",
      cert_data       => {
        common_name => $common_name,
        # comma separated list of DNS names 
        # https://www.rfc-editor.org/rfc/rfc6125#section-6.4.4
        alt_names => $alt_names,
      }
    }
    $merged_config = deep_merge($vault_certs_defaults + $paths, $config)
    vault_cert { $subdomain:
      * => $merged_config,
    }
  }

  file { '/var/lib/docker':
    ensure => 'directory',
    mode   => '0711',
    subscribe => Service['docker'],
  }

  file { '/var/lib/docker/volumes/paperless-ngx_consume/_data':
    ensure => 'directory',
    mode   => '0771',
    subscribe => Service['docker'],
  }

}

node 'ognongle.home.arpa' {
  $classes.include

  # dota 2: clicking alt while moving a unit will make it move in the direction,
  # disregarding pathing
  file { '/opt/steam/user/steamapps/common/dota 2 beta/game/dota/cfg/autoexec.cfg':
    content => 'cl_dota_alt_unit_movetodirection 1',
    owner   => 'user',
    group   => 'user',
  }

  # automatically enable nvidia accounting mode, required by scalene 
  # to accurately calculate the per process GPU usage
  systemd::manage_unit { 'nvidia-accounting-mode.service':
    unit_entry => {
      'Description' => 'Enable nvidia accounting mode',
      'Requires' => 'nvidia-persistenced.service',
      'After' => 'nvidia-persistenced.service',
    },
    service_entry => {
      'Type' => 'oneshot',
      'ExecStart' => '/usr/bin/nvidia-smi --accounting-mode=ENABLED',
    },
    install_entry => {
      'WantedBy' => 'multi-user.target',
    },
    enable => true,
    active => true,
  }

}
