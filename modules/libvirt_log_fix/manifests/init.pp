# Class: libvirt_log_fix
#
# This class addresses the issue of recurring kernel log messages related to Lockdown LSM
# when a libvirt domain is running with virt-manager open. It manages the rsyslog configuration
# for rpc-libvirtd and ensures that the rsyslog service is running.
#
# Note: The specific issue is the recurring message "Lockdown: rpc-worker: debugfs access is
# restricted; see man kernel_lockdown.7" in the kernel log.
#
# Parameters:
#   None
#
# Example Usage:
#   class { 'libvirt_log_fix': }
#
class libvirt_log_fix {
  # file - Manage the rsyslog configuration file for rpc-libvirtd.
  #
  # Parameters:
  #   - path: The path to the rsyslog configuration file.
  #   - ensure: Specifies the desired state of the file (default: 'present').
  #   - content: The content of the rsyslog configuration file.
  #   - notify: Specifies services that should be notified after the file is applied.
  #
  file { '/etc/rsyslog.d/10-rpc-libvirtd.conf':
    ensure  => present,
    content => 'puppet:///modules/libvirt/10-rpc-libvirtd.conf',
    notify  => Service['rsyslog'],
  }

  # service - Manage the rsyslog service.
  #
  # Parameters:
  #   - ensure: Specifies the desired state of the service (default: 'running').
  #
  service { 'rsyslog':
    ensure => running,
  }
}
