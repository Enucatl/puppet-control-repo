class profile::dropbear_initramfs (
  Array[String] $authorized_keys = [],
) {
  file { '/etc/dropbear/initramfs/authorized_keys':
    ensure  => file,
    owner   => 'root',
    group   => 'root',
    mode    => '0600',
    content => epp('profile/dropbear_authorized_keys.epp', { 'authorized_keys' => $authorized_keys }),
    notify  => Exec['update-initramfs'],
  }

  exec { 'update-initramfs':
    command     => '/usr/sbin/update-initramfs -u',
    refreshonly => true,
  }
}
