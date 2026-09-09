class profile::vllm_weekly_window (
  String  $script_path    = '/usr/local/sbin/vllm-weekly-window',
  String  $timer_calendar = 'Mon *-*-* 01:05:00 UTC',
  Integer $window_seconds = 3600,
) {
  include profile::proxmox_orchestration

  class { 'proxmox_workflows::vllm_weekly_window':
    script_path    => $script_path,
    timer_calendar => $timer_calendar,
    window_seconds => $window_seconds,
  }
  contain proxmox_workflows::vllm_weekly_window
}
