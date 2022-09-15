class dns::client (String $server=$facts['networking']['dhcp']) {
    dnsmasq::conf { 'local-dns':
        ensure  => present,
        content => "server=$server\nno-resolv",
    }

    systemd::dropin_file { 'sshd.conf':
      unit    => 'sshd.service',
      content => "puppet:///modules/dns/sshd.override.conf",
      notify  => Service["sshd"],
    }
}
