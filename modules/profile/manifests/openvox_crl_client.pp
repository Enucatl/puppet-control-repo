# Fetch signed CRLs independently of the agent's authenticated Puppet endpoint.
class profile::openvox_crl_client (
  Hash $config,
) {
  include profile::openvox_crl

  file { '/etc/openvox-crl/client.json':
    ensure  => file,
    owner   => 'root',
    group   => 'root',
    mode    => '0644',
    content => to_json($config),
    require => File['/etc/openvox-crl'],
  }

  systemd::unit_file { 'openvox-crl-client.service':
    content => @(UNIT),
      [Unit]
      Description=Refresh the authenticated OpenVox CRL chain
      Wants=network-online.target
      After=network-online.target
      Before=puppet.service
      OnFailure=openvox-crl-alert@openvox-crl-client.service

      [Service]
      Type=oneshot
      User=root
      ExecStart=/opt/puppetlabs/puppet/bin/ruby /usr/local/lib/openvox-crl/crl-refresh.rb sync /etc/openvox-crl/client.json
      TimeoutStartSec=120
      StandardOutput=journal
      StandardError=journal
      | UNIT
    require => [
      File['/etc/openvox-crl/client.json'],
      File['/usr/local/lib/openvox-crl/crl-refresh.rb'],
      File['/var/lib/openvox-crl'],
    ],
  }

  systemd::dropin_file { 'openvox-crl.conf':
    unit           => 'puppet.service',
    notify_service => false,
    content        => @(UNIT),
      [Unit]
      Wants=openvox-crl-client.service
      After=openvox-crl-client.service
      | UNIT
    require        => Systemd::Unit_file['openvox-crl-client.service'],
  }

  systemd::unit_file { 'openvox-crl-client.timer':
    content => @(UNIT),
      [Unit]
      Description=Refresh the agent CRL chain every five minutes

      [Timer]
      OnBootSec=1min
      OnUnitActiveSec=5min
      Unit=openvox-crl-client.service

      [Install]
      WantedBy=timers.target
      | UNIT
    enable  => true,
    active  => true,
    require => Systemd::Unit_file['openvox-crl-client.service'],
  }
}
