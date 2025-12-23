class nfs_config (
  Hash $server_exports = {},
  Hash $client_mounts  = {},
) {
  # 1. Base NFS module and Kerberos setup
  include ::nfs

  create_resources('nfs::server::export', $server_exports)
  create_resources('nfs::client::mount', $client_mounts)
}
