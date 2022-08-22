class dns::hardcoded_client {
  dnsmasq::conf { 'local-dns':
    ensure  => present,
    content => "server=192.168.2.42\nno-resolv",
  }
}
