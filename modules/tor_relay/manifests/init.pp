# Class: tor_relay
#
# This class configures a Tor relay with specified parameters using the tor module.
#
# Parameters:
#   - $orport: The ORPort for the Tor relay.
#   - $nickname: The nickname for the Tor relay.
#   - $relay_bandwidth_rate: The relay bandwidth rate for the Tor relay.
#   - $relay_bandwidth_burst: The relay bandwidth burst for the Tor relay.
#   - $contact_info: The contact information for the Tor relay.
#   - $dir_port: The directory port for the Tor relay.
#   - $socks_port: The SOCKS port for the Tor relay.
#   - $metrics_port: The metrics port for the Tor relay.
#
# Example Usage:
#   class { 'tor_relay':
#     orport                => '9001',
#     nickname              => 'mytorrelay',
#     relay_bandwidth_rate  => '100 KB',
#     relay_bandwidth_burst => '200 KB',
#     contact_info          => 'admin@example.com',
#     dir_port              => '9030',
#     socks_port            => '9050',
#     metrics_port          => '9151',
#   }
#
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

  # Include the tor class with the use_upstream_repository parameter set to true.
  class { 'tor':
    use_upstream_repository => true,
  }

  # Configure the Tor relay daemon using the tor::daemon::relay resource.
  tor::daemon::relay { 'relay':
    port                  => $orport,
    nickname              => $nickname,
    relay_bandwidth_rate  => $relay_bandwidth_rate,
    relay_bandwidth_burst => $relay_bandwidth_burst,
    contact_info          => $contact_info,
  }

  # Configure the Tor directory daemon using the tor::daemon::directory resource.
  tor::daemon::directory { 'directory':
    port => $dir_port,
  }

  # Configure the Tor SOCKS daemon using the tor::daemon::socks resource.
  tor::daemon::socks { 'socks':
    port => $socks_port,
  }

  # Use an External Puppet (EPP) template to add extra configuration snippets.
  # In this case, allow connecting to the metrics_port only from localhost.
  tor::daemon::snippet {'extras':
    content => epp(
      'tor_relay/extras.epp',
      {'metrics_port' => $metrics_port}
    )
  }
}
