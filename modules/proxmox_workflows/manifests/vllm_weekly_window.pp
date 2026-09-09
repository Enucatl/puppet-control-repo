class proxmox_workflows::vllm_weekly_window (
  String  $script_path    = '/usr/local/sbin/vllm-weekly-window',
  String  $timer_calendar = 'Mon *-*-* 01:05:00 UTC',
  Integer $window_seconds = 3600,
) {
  include proxmox_workflows::library

  file { $script_path:
    ensure  => file,
    owner   => 'root',
    group   => 'root',
    mode    => '0755',
    source  => 'puppet:///modules/proxmox_workflows/vllm_weekly_window.py',
    require => Class['proxmox_workflows::library'],
  }

  systemd::unit_file { 'vllm-weekly-window.service':
    content => @("UNIT"),
      [Unit]
      Description=Run the weekly vLLM processing window
      Wants=network-online.target
      After=network-online.target

      [Service]
      Type=oneshot
      ExecStart=${script_path} --window-seconds ${window_seconds}
      TimeoutStartSec=infinity
      | UNIT
    require => [Class['proxmox_workflows::library'], File[$script_path]],
  }

  systemd::unit_file { 'vllm-weekly-window.timer':
    content => @("UNIT"),
      [Unit]
      Description=Run vLLM processing weekly

      [Timer]
      OnCalendar=${timer_calendar}
      Persistent=true
      Unit=vllm-weekly-window.service

      [Install]
      WantedBy=timers.target
      | UNIT
    enable  => true,
    active  => true,
    require => Systemd::Unit_file['vllm-weekly-window.service'],
  }
}
