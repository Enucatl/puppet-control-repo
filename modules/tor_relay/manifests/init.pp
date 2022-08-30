class tor_relay (
  $orport,
  $nickname,
  $relay_bandwidth_rate,
  $relay_bandwidth_burst,
  $contact_info,
  $dir_port,
  $socks_port,
) {

  class { 'tor':
    use_upstream_repository => true,
  }

  tor::daemon::relay { 'relay':
    port                  => Integer($orport),
    nickname              => $nickname,       
    relay_bandwidth_rate  => Integer($relay_bandwidth_rate),       
    relay_bandwidth_burst => Integer($relay_bandwidth_burst),       
    contact_info          => $contact_info,       
  }

  tor::daemon::directory { 'directory':
    port => Integer($dir_port),
  }

  tor::daemon::socks { 'socks':
    port => Integer($socks_port),
  }

  tor::daemon::snippet {'snippet':
    content => 'ExitRelay 0'
  }

}
