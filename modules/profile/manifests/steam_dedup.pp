# Share duplicate game extents while preserving independently writable Steam files.
class profile::steam_dedup {
  package { ['duperemove', 'time']:
    ensure => installed,
  }

  file { '/usr/local/sbin/steam-dedup':
    ensure => file,
    owner  => 'root',
    group  => 'root',
    mode   => '0755',
    source => 'puppet:///modules/profile/steam-dedup',
  }

  systemd::unit_file { 'steam-dedup.service':
    content => @("UNIT"),
      [Unit]
      Description=Deduplicate independent Steam installations on XFS
      RequiresMountsFor=/etc/wolf/profile-data /var/lib/steam-dedup

      [Service]
      Type=oneshot
      StateDirectory=steam-dedup
      StateDirectoryMode=0700
      UMask=0077
      ExecStart=/usr/bin/time -v /usr/local/sbin/steam-dedup
      Nice=19
      IOSchedulingClass=idle
      IOWeight=10
      CPUQuota=50%
      MemoryHigh=1G
      MemoryMax=2G
      MemorySwapMax=0
      MemoryAccounting=true
      CPUAccounting=true
      IOAccounting=true
      OOMPolicy=stop
      TimeoutStartSec=infinity
      StandardOutput=journal
      StandardError=journal
      | UNIT
    require => [Package['duperemove'], Package['time'], File['/usr/local/sbin/steam-dedup']],
  }

  # systemd never starts a second instance of the same active oneshot service.
  systemd::unit_file { 'steam-dedup.timer':
    content => @("UNIT"),
      [Unit]
      Description=Deduplicate Steam games monthly

      [Timer]
      OnCalendar=*-*-01 04:00:00 UTC
      RandomizedDelaySec=1h
      Persistent=true
      Unit=steam-dedup.service

      [Install]
      WantedBy=timers.target
      | UNIT
    enable  => true,
    active  => true,
    require => Systemd::Unit_file['steam-dedup.service'],
  }
}
