class profile::atlas::certificates {
  require profile::common::certs

  $openvox = '/usr/bin/python3 /opt/docker/puppet-control-repo/proxmox/atlas-apply-certificates.py openvox'
  exec { 'apply-openvox-certificate':
    command => $openvox,
    unless  => "${openvox} --check",
    timeout => 1800,
  }

  ['vault', 'ipa'].each |String $service| {
    $apply = "/usr/bin/python3 /opt/docker/puppet-control-repo/proxmox/atlas-apply-certificates.py ${service}"
    exec { "apply-${service}-certificate":
      command => $apply,
      unless  => "${apply} --check",
      require => Vault_cert["${service}-transport"],
      timeout => 1800,
    }
  }
}
