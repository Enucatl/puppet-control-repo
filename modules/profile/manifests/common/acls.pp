class profile::common::acls (
  Hash $posix_acls = {},
) {
  require posix_acl::requirements

  $posix_acls.each |String $path, Hash $config| {
    posix_acl { $path:
      * => $config,
    }
  }
}
