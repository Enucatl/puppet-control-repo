class tor_relay {

  $orport = Deferred('vault_key', ["${vault_addr}/v1/secret/data/tor", 'ORPort', 'cpe-id', 'v2'])
  $nickname = Deferred('vault_key', ["${vault_addr}/v1/secret/data/tor", 'Nickname', 'cpe-id', 'v2'])
  $relay_bandwidth_rate = Deferred('vault_key', ["${vault_addr}/v1/secret/data/tor", 'RelayBandwidthRate', 'cpe-id', 'v2'])
  $relay_bandwidth_burst = Deferred('vault_key', ["${vault_addr}/v1/secret/data/tor", 'RelayBandwidthBurst', 'cpe-id', 'v2'])
  $contact_info = Deferred('vault_key', ["${vault_addr}/v1/secret/data/tor", 'ContactInfo', 'cpe-id', 'v2'])
  $dir_port = Deferred('vault_key', ["${vault_addr}/v1/secret/data/tor", 'DirPort', 'cpe-id', 'v2'])
  $socks_port = Deferred('vault_key', ["${vault_addr}/v1/secret/data/tor", 'SocksPort', 'cpe-id', 'v2'])

  tor::daemon::relay { 'relay':
    port     => $orport,
    nickname => $nickname,       
    relay_bandwidth_rate => $relay_bandwidth_rate,       
    relay_bandwidth_burst => $relay_bandwidth_burst,       
    contact_info => $contact_info,       
  }

  tor::daemon::directory { 'directory':
    port => $dir_port,
  }

  tor::daemon::socks { 'socks':
    port => $socks_port,
  }

  tor::daemon::snippet {'snippet':
    content => 'ExitRelay 0'
  }

}
