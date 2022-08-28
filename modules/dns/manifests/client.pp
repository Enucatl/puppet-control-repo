class dns::client (String $server=$facts['networking']['dhcp']) {
  dnsmasq::conf { 'local-dns':
    ensure  => present,
    content => "server=$server\nno-resolv",
  }
}
