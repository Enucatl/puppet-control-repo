class profile::proxmox_vfio_passthrough (
  Array[String[1]] $pci_ids,
  String[1] $config_name,
  Boolean $blacklist_nouveau = true,
) {
  file { "/etc/modprobe.d/${config_name}.conf":
    ensure  => file,
    owner   => 'root',
    group   => 'root',
    mode    => '0644',
    content => epp('profile/proxmox_vfio_passthrough.conf.epp', {
        'blacklist_nouveau' => $blacklist_nouveau,
        'pci_ids'           => $pci_ids,
    }),
    notify  => Exec['update-initramfs-all'],
  }

  exec { 'update-initramfs-all':
    command     => '/usr/sbin/update-initramfs -u -k all',
    refreshonly => true,
    notify      => Exec['proxmox-boot-tool-refresh'],
  }

  exec { 'proxmox-boot-tool-refresh':
    command     => '/usr/sbin/proxmox-boot-tool refresh',
    refreshonly => true,
  }
}
