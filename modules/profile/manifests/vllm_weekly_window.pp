class profile::vllm_weekly_window (
  String  $script_path    = '/usr/local/sbin/vllm-weekly-window',
  String  $timer_calendar = 'Mon *-*-* 01:05:00 UTC',
  Integer $window_seconds = 3600,
  Pattern[/\A[a-zA-Z0-9][a-zA-Z0-9.-]*\z/] $vm_host = 'complex.home.arpa',
  Integer[1] $vm_id       = 200,
) {
  include profile::proxmox_orchestration

  class { 'proxmox_workflows::vllm_weekly_window':
    script_path    => $script_path,
    timer_calendar => $timer_calendar,
    window_seconds => $window_seconds,
    vm_host        => $vm_host,
    vm_id          => $vm_id,
  }
  contain proxmox_workflows::vllm_weekly_window
}
