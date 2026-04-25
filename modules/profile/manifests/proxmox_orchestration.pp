class profile::proxmox_orchestration (
  String $module_path = '/usr/local/lib/proxmox_orchestration.py',
) {
  file { $module_path:
    ensure => file,
    owner  => 'root',
    group  => 'root',
    mode   => '0644',
    source => 'puppet:///modules/profile/proxmox_orchestration.py',
  }
}
