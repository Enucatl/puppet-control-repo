class profile::common::identity {
  exec { 'restart_sssd_after_identity_configuration':
    command     => '/bin/systemctl restart sssd',
    refreshonly => true,
    require     => Class['freeipa::client'],
  }

  # Enable SSSD Kerberos ticket renewal so NFS krb5p mounts stay accessible
  # beyond the default 24h TGT lifetime. FreeIPA already issues 7-day renewable
  # tickets; this tells SSSD to actually renew them every hour. Pin FreeIPA
  # lookups so clients do not reverse-canonicalize binds to the Docker host PTR.
  # The Sssd augeas lens stores section content as children of 'target' nodes,
  # where the target node value is the section name. Select the right section
  # with the predicate target[.='domain/home.arpa'].
  augeas { 'sssd_krb5_renewal':
    context => '/files/etc/sssd/sssd.conf',
    changes => [
      "set target[.='domain/home.arpa']/ipa_server freeipa.home.arpa",
      "set target[.='domain/home.arpa']/ldap_sasl_canonicalize false",
      "set target[.='domain/home.arpa']/krb5_canonicalize false",
      "set target[.='domain/home.arpa']/krb5_renewable_lifetime 7d",
      "set target[.='domain/home.arpa']/krb5_renew_interval 1h",
    ],
    require => Class['freeipa::client'],
    notify  => Exec['restart_sssd_after_identity_configuration'],
  }

  # Some FreeIPA maintenance paths, including certmonger helpers and ipa CLI
  # calls, use Kerberos/OpenLDAP directly instead of going through SSSD. Keep
  # those callers from reverse-canonicalizing ldap://freeipa.home.arpa to the
  # Docker host PTR. Disable DNS canonization here because FreeIPA, Docker,
  # and other services share one IP via containers, and we were seeing errors
  # like "Error binding to ""ldap://freeipa.home.arpa/"": Local error." Kerberos
  # still checks the service principal, so this is a reasonable tradeoff in a
  # controlled internal network.
  augeas { 'krb5_disable_dns_canonicalization':
    context => '/files/etc/krb5.conf',
    changes => [
      'set libdefaults/dns_canonicalize_hostname false',
      'set libdefaults/rdns false',
    ],
    require => Class['freeipa::client'],
    notify  => Exec['restart_sssd_after_identity_configuration'],
  }

  # Debian enables SSSD responder sockets by preset, but ipa-client-install
  # configures the responders in sssd.conf's services line. Keep one activation
  # model to avoid sssd_check_socket_activated_responders warnings at boot.
  service { [
      'sssd-autofs.socket',
      'sssd-nss.socket',
      'sssd-pac.socket',
      'sssd-pam.socket',
      'sssd-ssh.socket',
      'sssd-sudo.socket',
    ]:
    ensure  => stopped,
    enable  => false,
    require => Class['freeipa::client'],
  }
}
