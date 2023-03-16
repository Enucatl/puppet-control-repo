class libvirt_log_fix {
  file { '/etc/rsyslog.d/10-rpc-libvirtd.conf':
    ensure  => present,
    content => 'puppet:///modules/libvirt/10-rpc-libvirtd.conf',
}
