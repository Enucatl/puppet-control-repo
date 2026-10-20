class profile::desktop_printing {
  package { 'cups':
    ensure => installed,
  }

  service { 'cups':
    ensure  => running,
    enable  => true,
    require => Package['cups'],
  }

  exec { 'configure-brother-printer':
    command     => '/usr/sbin/lpadmin -p Brother_MFC_L2750DW -E -v ipp://printer.home.arpa/ipp/print -m everywhere -D "Brother MFC-L2750DW" -o printer-is-shared=false',
    unless      => '/usr/bin/lpstat -v Brother_MFC_L2750DW | /usr/bin/grep -Fxq "device for Brother_MFC_L2750DW: ipp://printer.home.arpa/ipp/print"',
    provider    => shell,
    environment => ['LC_ALL=C'],
    require     => Service['cups'],
  }

  exec { 'brother-long-edge-duplex':
    command     => '/usr/sbin/lpadmin -p Brother_MFC_L2750DW -o sides-default=two-sided-long-edge -o Duplex=DuplexNoTumble',
    unless      => "/usr/bin/lpoptions -p Brother_MFC_L2750DW | /usr/bin/grep -Eq '(^|[[:space:]])sides=two-sided-long-edge([[:space:]]|$)'",
    provider    => shell,
    environment => ['LC_ALL=C'],
    require     => Exec['configure-brother-printer'],
  }

  exec { 'default-brother-printer':
    command     => '/usr/sbin/lpadmin -d Brother_MFC_L2750DW',
    unless      => '/usr/bin/lpstat -d | /usr/bin/grep -Fxq "system default destination: Brother_MFC_L2750DW"',
    provider    => shell,
    environment => ['LC_ALL=C'],
    require     => Exec['configure-brother-printer'],
  }
}
