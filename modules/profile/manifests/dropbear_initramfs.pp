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

  if $network_device != undef and $network_ip != undef {
    file { '/etc/initramfs-tools/conf.d/dropbear-network.conf':
      ensure  => file,
      owner   => 'root',
      group   => 'root',
      mode    => '0644',
      content => "DEVICE=${network_device}\nIP=${network_ip}\n",
      notify  => Exec['update-initramfs'],
    }
  }

  exec { 'update-initramfs':
    command     => '/usr/sbin/update-initramfs -u',
    refreshonly => true,
  }
}
