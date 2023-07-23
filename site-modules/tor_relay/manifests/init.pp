class tor_relay (
  $orport,
  $nickname,
  $relay_bandwidth_rate,
  $relay_bandwidth_burst,
  $contact_info,
  $dir_port,
  $socks_port,
  $metrics_port,
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

  tor::daemon::snippet {'extras':
    content => epp(
      'tor_relay/extras.epp',
      {'metrics_port' => $metrics_port}
    )
  }

}
