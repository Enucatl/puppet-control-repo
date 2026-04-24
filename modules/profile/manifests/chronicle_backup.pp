class profile::chronicle_backup (
  String  $script_path   = '/usr/local/sbin/chronicle-backup-orchestrator',
  String  $backup_job_id = 'pbs-chronicle-weekly',
  String  $timer_calendar = 'Mon *-*-* 01:00:00',
) {
  file { $script_path:
    ensure => file,
    owner  => 'root',
    group  => 'root',
    mode   => '0755',
    source => 'puppet:///modules/profile/chronicle_backup_orchestrator.py',
  }

  exec { "disable ${backup_job_id} scheduler":
    command => "/usr/bin/pvesh set /cluster/backup/${backup_job_id} --enabled 0",
    onlyif  => "/usr/bin/pvesh get /cluster/backup/${backup_job_id} --output-format json | /usr/bin/jq -e '.enabled != 0'",
    path    => ['/usr/bin', '/bin'],
    before  => Systemd::Unit_file['chronicle-backup.timer'],
  }

  systemd::unit_file { 'chronicle-backup.service':
    content => @("UNIT"),
      [Unit]
      Description=Run the Chronicle Proxmox backup orchestration
      Wants=network-online.target
      After=network-online.target

      [Service]
      Type=oneshot
      ExecStart=${script_path} --backup-job-id ${backup_job_id}
      TimeoutStartSec=6h
      | UNIT
    require => File[$script_path],
  }

  systemd::unit_file { 'chronicle-backup.timer':
    content => @("UNIT"),
      [Unit]
      Description=Run Chronicle Proxmox backup weekly

      [Timer]
      OnCalendar=${timer_calendar}
      Persistent=true
      Unit=chronicle-backup.service

      [Install]
      WantedBy=timers.target
      | UNIT
    enable  => true,
    active  => true,
    require => Systemd::Unit_file['chronicle-backup.service'],
  }
}
