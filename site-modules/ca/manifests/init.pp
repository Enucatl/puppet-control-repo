class ca {

  ca_cert::ca { 'puppet_ca':
    ensure => 'trusted',
    source => 'puppet:///modules/ca/puppet-ca.crt',
  }

  ca_cert::ca { 'root_2022_ca':
    ensure => 'trusted',
    source => 'puppet:///modules/ca/root_2022_ca.crt',
  }

  file { '/etc/ssh/ssh_ca.pub':
    source => 'puppet:///modules/ca/ssh_ca.pub',
    owner  => 'root',
    group  => 'root',
    mode   => '0644',
    before => Class['ssh'],
  }

}
