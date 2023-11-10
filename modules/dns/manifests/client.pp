# Class: dns::client
#
# This class configures a DNS client using dnsmasq, specifying IPv6 and IPv4 DNS servers.
#
# Parameters:
#   - $server_ipv6: The IPv6 address of the DNS server.
#   - $server_ipv4: The IPv4 address of the DNS server (default: obtained from DHCP).
#
# Example Usage:
#   class { 'dns::client':
#     server_ipv6 => '2001:db8::1',
#   }
#
class dns::client (
  String $server_ipv6,
  String $server_ipv4 = $facts['networking']['dhcp'],
) {
  # dnsmasq::conf - Manage the dnsmasq configuration for local DNS.
  #
  # Parameters:
  #   - ensure: Specifies the desired state of the configuration file (default: 'present').
  #   - content: The content of the dnsmasq configuration file.
  #
  dnsmasq::conf { 'local-dns':
    ensure  => present,
    content => "server=$server_ipv6\nserver=$server_ipv4\nno-resolv",
  }

  # systemd::dropin_file - Manage a drop-in configuration file for the sshd service.
  #
  # Parameters:
  #   - unit: The systemd unit to which the drop-in file belongs.
  #   - source: The source URL for the drop-in configuration file.
  #   - require: Specifies dependencies that must be met before applying the drop-in file.
  #   - notify: Specifies services that should be notified after the drop-in file is applied.
  #
  systemd::dropin_file { 'sshd.conf':
    unit    => 'sshd.service',
    source  => "puppet:///modules/dns/sshd.override.conf",
    require => Class["ssh"],
    notify  => Service[$ssh::server::service_name],
  }
}
