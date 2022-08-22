class client {
  dnsmasq::conf { 'local-dns':
    ensure  => present,
    content => "server=${facts['networking']['dhcp']}\nno-resolv",
  }
}
