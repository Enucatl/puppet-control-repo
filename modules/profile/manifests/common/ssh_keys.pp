class profile::common::ssh_keys (
  Hash $authorized_keys = {},
) {
  if !empty($authorized_keys) {
    create_resources(ssh_authorized_key, $authorized_keys)
  }
}
