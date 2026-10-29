class profile::desktop {
  systemd::udev::rule { '41-nitrokey.rules':
    rules => [file('profile/udev/41-nitrokey.rules')],
  }

  file { '/etc/netplan/99-networkd.yaml':
    ensure  => file,
    owner   => 'root',
    group   => 'root',
    mode    => '0600',
    content => @("NETPLAN"),
      network:
        version: 2
        renderer: networkd
        ethernets:
          eth0:
            accept-ra: true
      | NETPLAN
    notify  => Exec['generate-desktop-networkd'],
  }

  exec { 'generate-desktop-networkd':
    command     => '/usr/sbin/netplan generate',
    refreshonly => true,
  }

  service { ['NetworkManager', 'NetworkManager-wait-online']:
    ensure  => stopped,
    enable  => mask,
    require => Exec['generate-desktop-networkd'],
    before  => Service['systemd-networkd'],
  }

  service { 'systemd-networkd':
    ensure    => running,
    enable    => true,
    subscribe => Exec['generate-desktop-networkd'],
  }

  service { 'systemd-networkd-wait-online':
    enable  => true,
    require => Service['systemd-networkd'],
  }

  file { '/home/user/Documents':
    ensure  => directory,
    owner   => 'user',
    group   => 'user',
    mode    => '0750',
    require => File['/home/user'],
    before  => [Posix_acl['/home/user/Documents'], Nfs::Server::Export['/home/user/Documents']],
  }
}
