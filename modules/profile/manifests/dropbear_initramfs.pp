class profile::dropbear_initramfs (
  Array[String] $authorized_keys = [],
  Optional[String] $network_device = undef,
  Optional[String] $network_ip = undef,
) {
  file { '/etc/dropbear/initramfs/authorized_keys':
    ensure  => file,
    owner   => 'root',
    group   => 'root',
    mode    => '0600',
    content => epp('profile/dropbear_authorized_keys.epp', { 'authorized_keys' => $authorized_keys }),
    notify  => Exec['update-initramfs'],
  }

  if $network_device != undef {
    augeas { 'initramfs_network_device':
      context => '/files/etc/initramfs-tools/initramfs.conf',
      changes => "set DEVICE '${network_device}'",
      notify  => Exec['update-initramfs'],
    }
  }

  if $network_ip != undef {
    augeas { 'initramfs_network_ip':
      context => '/files/etc/initramfs-tools/initramfs.conf',
      changes => "set IP '${network_ip}'",
      notify  => Exec['update-initramfs'],
    }
  }

  exec { 'update-initramfs':
    command     => '/usr/sbin/update-initramfs -u',
    refreshonly => true,
  }
}
