class tor_relay (
  Integer $orport,
  String $nickname,
  Integer $relay_bandwidth_rate,
  Integer $relay_bandwidth_burst,
  String $contact_info,
  Integer $dirport,
  Integer $socks_port,
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
