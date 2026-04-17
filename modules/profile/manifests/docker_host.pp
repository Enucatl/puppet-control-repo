class profile::docker_host (
  Hash             $git_deploy_projects = {},
  Optional[String] $maxmind_account_id  = undef,
  Optional[String] $maxmind_license_key = undef,
) {

  require profile::common

  $git_deploy_projects.each |String $project, Hash $params| {
    profile::docker_deploy { $project:
      * => $params,
    }
  }

  # Ensure Service is running (The 'docker' class usually handles this,
  # but site.pp had explicit declaration, so preserving it here)
  service { 'docker':
    ensure => running,
    enable => true,
  }

  if !empty($maxmind_account_id) and !empty($maxmind_license_key) {
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
        'account_id'  => $maxmind_account_id,
        'license_key' => $maxmind_license_key,
      }),
      require => Package['geoipupdate'],
    }

    exec { 'geoipupdate-initial':
      command => '/usr/bin/geoipupdate',
      creates => '/var/lib/geoip/GeoLite2-Country.mmdb',
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
