# Compatibility entry point for the installed shared library path.
class profile::proxmox_orchestration (
  String $module_path = '/usr/local/lib/proxmox_orchestration.py',
) {
  class { 'proxmox_workflows::library':
    module_path => $module_path,
  }
  contain proxmox_workflows::library
}
