class tor_relay (Hash $vault_hash) {

  tor::daemon::relay { 'relay':
    port     => $vault_hash['ORPort'],
    nickname => $vault_hash['Nickname'],       
    relay_bandwidth_rate => $vault_hash['RelayBandwidthRate'],       
    relay_bandwidth_burst => $vault_hash['RelayBandwidthBurst'],       
    contact_info => $vault_hash['ContactInfo'],       
  }

  tor::daemon::directory { 'directory':
    port => $vault_hash['DirPort'],
  }

  tor::daemon::socks { 'socks':
    port => $vault_hash['SocksPort'],
  }

  tor::daemon::snippet {'snippet':
    content => 'ExitRelay 0'
  }

}
