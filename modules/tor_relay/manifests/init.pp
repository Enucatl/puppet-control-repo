class tor_relay {

  $orport = Deferred('vault_key', ["${vault_addr}/v1/secret/data/tor", 'cert', 'ORPort', 'v2'])
  $nickname = Deferred('vault_key', ["${vault_addr}/v1/secret/data/tor", 'cert', 'Nickname', 'v2'])
  $relay_bandwidth_rate = Deferred('vault_key', ["${vault_addr}/v1/secret/data/tor", 'cert',  'RelayBandwidthRate', 'v2'])
  $relay_bandwidth_burst = Deferred('vault_key', ["${vault_addr}/v1/secret/data/tor", 'cert',  'RelayBandwidthBurst', 'v2'])
  $contact_info = Deferred('vault_key', ["${vault_addr}/v1/secret/data/tor", 'cert', 'ContactInfo', 'v2'])
  $dir_port = Deferred('vault_key', ["${vault_addr}/v1/secret/data/tor", 'cert', 'DirPort', 'v2'])
  $socks_port = Deferred('vault_key', ["${vault_addr}/v1/secret/data/tor", 'cert', 'SocksPort', 'v2'])

  include tor

  tor::daemon::relay { 'relay':
    port                  => $orport,
    nickname              => $nickname,       
    address               => "",
    #bandwidth_rate        => 0,
    #bandwidth_burst       => 0,       
    relay_bandwidth_rate  => $relay_bandwidth_rate,       
    relay_bandwidth_burst => $relay_bandwidth_burst,       
    contact_info          => $contact_info,       
  }

  tor::daemon::directory { 'directory':
    port => $dir_port,
    port_front_page => "",
  }

  tor::daemon::socks { 'socks':
    port => $socks_port,
  }

  tor::daemon::snippet {'snippet':
    content => 'ExitRelay 0'
  }

}
