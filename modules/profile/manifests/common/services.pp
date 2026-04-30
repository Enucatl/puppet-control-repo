class profile::common::services (
  Hash $resources = {},
) {
  if !empty($resources) {
    create_resources(service, $resources)
  }
}
