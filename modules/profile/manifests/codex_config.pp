# Shared defaults; Codex owns the writable user config and runtime state.
class profile::codex_config {
  file { '/etc/codex':
    ensure => directory,
    owner  => 'root',
    group  => 'root',
    mode   => '0755',
  }

  file { '/etc/codex/config.toml':
    ensure  => file,
    owner   => 'root',
    group   => 'root',
    mode    => '0644',
    source  => 'puppet:///modules/profile/codex-config.toml',
    require => File['/etc/codex'],
  }
}
