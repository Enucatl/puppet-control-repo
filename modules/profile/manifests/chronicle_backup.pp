class profile::chronicle_backup (
  String  $script_path   = '/usr/local/sbin/chronicle-backup-orchestrator',
  String  $backup_job_id = 'pbs-chronicle-weekly',
  String  $timer_calendar = 'Mon *-*-* 01:00:00',
) {
  include profile::proxmox_orchestration

  class { 'proxmox_workflows::chronicle_backup':
    script_path    => $script_path,
    backup_job_id  => $backup_job_id,
    timer_calendar => $timer_calendar,
  }
  contain proxmox_workflows::chronicle_backup
}
