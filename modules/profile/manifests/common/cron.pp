class profile::common::cron (
  Hash $jobs = {},
) {
  if !empty($jobs) {
    resources { 'cron': purge => true }
    create_resources(cron, $jobs)
  }
}
