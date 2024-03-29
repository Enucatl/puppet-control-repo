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
    # set default domain to subdomain.nuc10i7fnh.home.arpa
    # unless explicitly specified
    $default_value = "${subdomain}.${trusted['certname']}"
    $paths = {
      cert_chain_file => "${vault_certs_default_location}/${default_value}/fullchain.pem",
      key_file        => "${vault_certs_default_location}/${default_value}/privkey.pem",
      cert_data       => {
        common_name => $default_value,
        # comma separated list of DNS names 
        # https://www.rfc-editor.org/rfc/rfc6125#section-6.4.4
        alt_names => $default_value,
      }
    }
    $vault_cert_config = deep_merge($vault_certs_defaults + $paths, $config)
    vault_cert { $subdomain:
      * => $vault_cert_config,
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

  create_resources(sysctl, lookup('sysctl_hash'))
  create_resources(libvirt::network, lookup('libvirt::networks'))
  create_resources(cron, lookup('cronjobs'))

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
    active => false,
  }

  # prevent goa-daemon from writing one million lines a day in the logs
  # https://askubuntu.com/questions/343746/disable-and-delete-goa-daemon
  file { '/usr/share/dbus-1/services/org.gnome.OnlineAccounts.service':
    ensure  => file,
    content => @("ONLINEACCOUNTS"/L)
    [D-BUS Service]
    Name=org.gnome.OnlineAccounts
    #disabled by puppet
    #Exec=/usr/libexec/goa-daemon
    Exec=/usr/bin/false
    | ONLINEACCOUNTS
  }

  create_resources(sysctl, lookup('sysctl_hash'))
  create_resources(libvirt::network, lookup('libvirt::networks'))
}
