class dns::client (
  String $server_ipv6,
  String $server_ipv4=$facts['networking']['dhcp'],
) {
    dnsmasq::conf { 'local-dns':
        ensure  => present,
        content => "server=$server_ipv6\nserver=$server_ipv4\nno-resolv",
    }

    systemd::dropin_file { 'sshd.conf':
      unit   => 'sshd.service',
      source => "puppet:///modules/dns/sshd.override.conf",
      require => Class["ssh"],
      notify => Service[$ssh::server::service_name],
    }
}
