class profile::dropbear_initramfs (
  Array[String] $authorized_keys = [],
) {
  file { '/etc/dropbear/initramfs/authorized_keys':
    ensure  => file,
    owner   => 'root',
    group   => 'root',
    mode    => '0600',
    content => join($authorized_keys.map |$key| { "no-port-forwarding,no-agent-forwarding,no-x11-forwarding,command=\"/usr/bin/zfsunlock\" ${key}" }, "\n") + "\n",
    notify  => Exec['update-initramfs'],
  }

  exec { 'update-initramfs':
    command     => '/usr/sbin/update-initramfs -u',
    refreshonly => true,
  }
}
