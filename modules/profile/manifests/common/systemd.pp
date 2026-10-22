class profile::common::systemd (
  Hash $units         = {},
  Hash $user_services = {},
) {
  if $trusted['certname'] in ['atlas.home.arpa', 'aperture.home.arpa', 'cerebrum.home.arpa'] {
    # Old agents still need the compatibility files until their package upgrade.
    exec { 'remove-openvox-crypto-workaround':
      command => '/bin/rm -f /etc/systemd/system/puppet.service.d/puppet-crypto.conf /usr/local/libexec/openvox-child-policy.rb /etc/puppetlabs/puppet/openssl.cnf',
      onlyif  => [
        '/usr/bin/dpkg --compare-versions "$(/usr/bin/dpkg-query -W -f=\'${Version}\' openvox-agent)" ge 9.1.1+homelab.28001-1',
        '/usr/bin/test -e /etc/systemd/system/puppet.service.d/puppet-crypto.conf -o -e /usr/local/libexec/openvox-child-policy.rb -o -e /etc/puppetlabs/puppet/openssl.cnf',
      ],
      notify  => Systemd::Daemon_reload['openvox-native-crypto'],
    }
    systemd::daemon_reload { 'openvox-native-crypto': }
  }

  $units.each |String $unit_name, Hash $config| {
    systemd::unit_file { $unit_name:
      * => $config,
    }
  }

  $user_services.each |String $service_name, Hash $config| {
    systemd::user_service { $service_name:
      * => $config,
    }

    Systemd::Unit_file[$config['unit']] ~> Systemd::User_service[$service_name]
  }
}
