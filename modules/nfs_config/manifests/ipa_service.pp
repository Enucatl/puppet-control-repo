# @summary Register an NFS service principal in FreeIPA and retrieve its keytab.
#
# Uses the node's own host principal (kinit -k) so no admin credentials are
# needed — IPA grants each enrolled host the right to manage services listed
# as managed-by that host.
#
# The two Exec resources are independently idempotent:
#   - ipa-service-add is skipped if the service already exists in IPA.
#   - ipa-getkeytab is skipped if the principal is already in the keytab.
#
# @param ipa_server FQDN of the FreeIPA server.
# @param fqdn       FQDN of this node. Defaults to the networking fact.
# @param keytab     Keytab file to write the service key into.
define nfs_config::ipa_service (
  String           $ipa_server,
  String           $fqdn    = $facts['networking']['fqdn'],
  Stdlib::Unixpath $keytab  = '/etc/krb5.keytab',
) {
  $principal = "nfs/${fqdn}"

  exec { "ipa-service-add ${principal}":
    command => "bash -c 'kinit -k && ipa service-add ${principal}'",
    unless  => "bash -c 'kinit -k && ipa service-show ${principal}'",
    path    => ['/usr/bin', '/usr/sbin', '/bin'],
  }

  exec { "ipa-getkeytab ${principal}":
    command => "bash -c 'kinit -k && ipa-getkeytab -s ${ipa_server} -p ${principal} -k ${keytab}'",
    unless  => "klist -k ${keytab} | grep -qF '${principal}'",
    path    => ['/usr/bin', '/usr/sbin', '/bin'],
    require => Exec["ipa-service-add ${principal}"],
  }
}
