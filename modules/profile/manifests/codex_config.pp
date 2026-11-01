# Shared defaults; Codex owns the writable user config and runtime state.
class profile::codex_config (
  String $username = 'user',
) {
  $home = $username ? { 'root' => '/root', default => "/home/${username}" }

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

  # Match the absolute target created by rake, even after it becomes dangling.
  exec { 'remove-dotfiles-codex-config-link':
    command => "/usr/bin/rm -- ${home}/.codex/config.toml",
    onlyif  => "/usr/bin/test \"$(/usr/bin/readlink -- ${home}/.codex/config.toml)\" = ${home}/.vim/dotfiles/codex/config.toml",
    require => File['/etc/codex/config.toml'],
  }

  Exec['remove-dotfiles-codex-config-link'] -> Vcsrepo <| title == "${home}/.vim" |>
  Exec['remove-dotfiles-codex-config-link'] -> Exec <| title == 'dotfiles-rake-links' |>
  Exec['remove-dotfiles-codex-config-link'] -> Exec <| title == 'sync-codex-plugins-user' |>
}
