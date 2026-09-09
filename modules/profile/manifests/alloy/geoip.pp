# Internal implementation. Credentials and enablement are resolved by profile::alloy.
class profile::alloy::geoip (
  String $account_id,
  String $license_key,
) {
  package { 'geoipupdate':
    ensure => installed,
  }

  file { '/var/lib/geoip':
    ensure => directory,
    owner  => 'root',
    group  => 'root',
    mode   => '0755',
  }

  file { '/etc/GeoIP.conf':
    ensure  => file,
    owner   => 'root',
    group   => 'root',
    mode    => '0600',
    content => epp('profile/GeoIP.conf.epp', {
      'account_id'  => $account_id,
      'license_key' => $license_key,
    }),
    require => Package['geoipupdate'],
  }

  exec { 'geoipupdate-initial':
    command => '/usr/bin/geoipupdate',
    creates => '/var/lib/geoip/GeoLite2-City.mmdb',
    require => [
      File['/etc/GeoIP.conf'],
      File['/var/lib/geoip'],
    ],
    before  => Service['alloy'],
  }

  systemd::unit_file { 'geoipupdate.service':
    content => @("UNIT"),
      [Unit]
      Description=Update MaxMind GeoIP databases
      Wants=network-online.target
      After=network-online.target

      [Service]
      Type=oneshot
      ExecStart=/usr/bin/geoipupdate
      | UNIT
    require => File['/etc/GeoIP.conf'],
  }

  systemd::unit_file { 'geoipupdate.timer':
    content => @("UNIT"),
      [Unit]
      Description=Weekly MaxMind GeoIP database update

      [Timer]
      OnCalendar=Sun *-*-* 03:00:00 UTC
      Persistent=true
      Unit=geoipupdate.service

      [Install]
      WantedBy=timers.target
      | UNIT
    enable  => true,
    active  => true,
    require => Systemd::Unit_file['geoipupdate.service'],
  }
}
