class nisdomainname (
  String $domain = 'home.arpa',
) {

  # 1. Set it immediately so sudo works right now
  exec { 'set_nis_domain_now':
    command => "nisdomainname ${domain}",
    unless  => "nisdomainname | grep -q '^${domain}$'",
    path    => ['/bin', '/usr/bin', '/sbin', '/usr/sbin'],
  }

  # 2. Create a systemd service to set it on boot
  file { '/etc/systemd/system/nisdomain.service':
    ensure  => file,
    content => "[Unit]
Description=Set NIS Domain Name
After=network.target

[Service]
Type=oneshot
ExecStart=/bin/nisdomainname ${domain}
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
",
    notify  => Service['nisdomain'],
  }

  # 3. Enable the service so it runs on reboot
  service { 'nisdomain':
    ensure  => running,
    enable  => true,
    require => File['/etc/systemd/system/nisdomain.service'],
  }
}
