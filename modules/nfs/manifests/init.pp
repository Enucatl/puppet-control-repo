# Class: nfs
#
# This class manages the configuration and service for NFS (Network File System).
# add option RPCSVCGSSDOPTS="-n" to fix startup of service rpc-svcgssd on debian hosts
#
# Parameters:
#   None
#
# Example Usage:
#   class { 'nfs': }
#
class nfs {
  # service - Manage the rpc-svcgssd service.
  #
  # Parameters:
  #   - ensure: Specifies the desired state of the service (default: 'running').
  #
  service { 'rpc-svcgssd':
    ensure => 'running',
  }

  # file - Manage the configuration file for nfs-common.
  #
  # Parameters:
  #   - path: The path to the nfs-common configuration file.
  #   - ensure: Specifies the desired state of the file (default: 'present').
  #   - source: The source URL for the configuration file.
  #   - before: Specifies resources that should be applied before this file.
  #   - notify: Specifies services that should be notified after this file is applied.
  #
  file { '/etc/default/nfs-common':
    ensure => 'present',
    source => 'puppet:///modules/nfs/nfs-common',
    before => Service['rpc-svcgssd'],
    notify => Service['rpc-svcgssd'],
  }
}
