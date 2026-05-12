class profile::docker_host (
  Hash                $git_deploy_projects         = {},
  Boolean             $scheduled_refresh           = false,
  String              $scheduled_refresh_calendar   = 'Sun *-*-01..07 04:00:00',
  Optional[Integer]   $maxmind_account_id           = undef,
  Optional[String]    $maxmind_license_key         = undef,
) {

  require profile::common

  $wolf_wol_magic_hex = 'ffffffffffff30560f5ea9de30560f5ea9de30560f5ea9de30560f5ea9de30560f5ea9de30560f5ea9de30560f5ea9de30560f5ea9de30560f5ea9de30560f5ea9de30560f5ea9de30560f5ea9de30560f5ea9de30560f5ea9de30560f5ea9de30560f5ea9de'
  $wolf_wol_rule = "-o eth0 -d 10.0.0.255/32 -p udp --dport 9 -m length --length 130 -m string --algo bm --hex-string '|${wolf_wol_magic_hex}|' -j ACCEPT"

  $git_deploy_projects.each |String $project, Hash $params| {
    $project_defaults = {
      'scheduled_refresh' => $scheduled_refresh,
      'refresh_calendar'  => $scheduled_refresh_calendar,
    }

    profile::docker_deploy { $project:
      * => $project_defaults + $params,
    }
  }

  # Ensure Service is running (The 'docker' class usually handles this,
  # but site.pp had explicit declaration, so preserving it here)
  service { 'docker':
    ensure => running,
    enable => true,
  }

  exec { 'allow-wolf-wol-directed-broadcast':
    command => "/usr/sbin/iptables -I DOCKER-USER 1 ${wolf_wol_rule}",
    unless  => "/usr/sbin/iptables -C DOCKER-USER ${wolf_wol_rule}",
    path    => ['/usr/sbin', '/usr/bin', '/sbin', '/bin'],
    require => Service['docker'],
  }

  exec { 'enable-docker-bridge-directed-broadcast':
    command => '/bin/sh -c \'for setting in /proc/sys/net/ipv4/conf/br-*/bc_forwarding; do [ -e "$setting" ] && echo 1 > "$setting"; done\'',
    unless  => '/bin/sh -c \'for setting in /proc/sys/net/ipv4/conf/br-*/bc_forwarding; do [ -e "$setting" ] || exit 0; [ "$(cat "$setting")" = "1" ] || exit 1; done\'',
    require => Service['docker'],
  }

  if $maxmind_account_id != undef and $maxmind_license_key != undef {
    $rendered_maxmind_account_id = String($maxmind_account_id)

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
        'account_id'  => $rendered_maxmind_account_id,
        'license_key' => $maxmind_license_key,
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

  # Alloy user management
  user { 'alloy':
    ensure  => present,
    groups  => 'docker',
    require => Package['alloy'],
    notify  => Service['alloy'],
  }

}
