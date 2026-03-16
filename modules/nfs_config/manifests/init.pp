class nfs_config (
  Hash   $server_exports = {},
  Hash   $client_mounts  = {},
  String $ipa_server     = 'freeipa.home.arpa',
) {
  include ::nfs

  create_resources('nfs::server::export', $server_exports)
  create_resources('nfs::client::mount', $client_mounts)

  if $server_exports != {} {
    nfs_config::ipa_service { 'nfs':
      ipa_server => $ipa_server,
    }
  }
}
