class checkmk {
  file { '/etc/check_mk/docker.cfg':
    ensure => 'present',
    source => 'puppet:///modules/checkmk/docker.cfg',
  }

  file { '/usr/lib/check_mk_agent/plugins/mk_docker.py':
    ensure => 'present',
    source => 'puppet:///modules/checkmk/mk_docker.py',
  }
}

