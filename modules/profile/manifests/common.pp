class profile::common {
  contain profile::common::certs
  contain profile::common::sysctl
  contain profile::common::cron
  contain profile::common::services
  contain profile::common::ssh_keys
  contain profile::common::systemd
  contain profile::common::udev
  contain profile::common::identity
  contain profile::common::acls

  # Public downloads use system TLS; Puppet and Vault retain their strict policy.
  Exec <| title == 'neovim-install' |> {
    environment => ['OPENSSL_CONF=/etc/ssl/openssl.cnf'],
  }
}
