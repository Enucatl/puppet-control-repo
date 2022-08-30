class tor_relay (
  String $orport,
  String $nickname,
  String $relay_bandwidth_rate,
  String $relay_bandwidth_burst,
  String $contact_info,
  String $dir_port,
  String $socks_port,
) {

  class { 'tor':
    use_upstream_repository => true,
  }

  tor::daemon::relay { 'relay':
    port                  => $orport,
    nickname              => $nickname,       
    relay_bandwidth_rate  => $relay_bandwidth_rate,       
    relay_bandwidth_burst => $relay_bandwidth_burst,       
    contact_info          => $contact_info,       
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
