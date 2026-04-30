class profile::common::sysctl (
  Hash $settings = {},
) {
  if !empty($settings) {
    create_resources(sysctl, $settings)
  }
}
