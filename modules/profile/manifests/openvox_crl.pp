# Shared files and bounded failure notifications for explicitly enabled CRL jobs.
class profile::openvox_crl {
  file { ['/etc/openvox-crl', '/usr/local/lib/openvox-crl', '/var/lib/openvox-crl']:
    ensure => directory,
    owner  => 'root',
    group  => 'root',
    mode   => '0755',
  }

  file { '/usr/local/lib/openvox-crl/crl-refresh.rb':
    ensure  => file,
    owner   => 'root',
    group   => 'root',
    mode    => '0644',
    source  => 'puppet:///modules/profile/openvox/crl-refresh.rb',
    require => File['/usr/local/lib/openvox-crl'],
  }

  systemd::unit_file { 'openvox-crl-alert@.service':
    content => @(UNIT),
      [Unit]
      Description=Notify root of failed CRL refresh for %i
      StartLimitIntervalSec=1h
      StartLimitBurst=1

      [Service]
      Type=oneshot
      ExecStart=/bin/sh -c 'printf "To: root\\nSubject: CRL refresh failed on %H (%i)\\n\\nCheck journalctl -u %i.service and CRL expiry immediately.\\n" | /usr/sbin/sendmail -t'
      | UNIT
  }
}
