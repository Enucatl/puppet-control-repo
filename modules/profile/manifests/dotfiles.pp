# Manages the user dotfiles repo at ~/.vim and installs symlinks via rake.
#
# Puppet keeps the repo at the latest HEAD (ensure => latest), which does a
# git fetch on every run. rake links uses `ln -sb` to back up any existing files
# (e.g. ~/.bashrc -> ~/.bashrc~) and replace them with symlinks.
class profile::dotfiles (
  String $username = 'user',
) {
  $home     = $username ? { 'root' => '/root', default => "/home/${username}" }
  $repo_dir = "${home}/.vim"

  ensure_packages(['rake'])

  # IPA creates homes at login; Puppet may run before the first login.
  file { $home:
    ensure => directory,
    owner  => $username,
    group  => $username,
    mode   => '0700',
  }

  vcsrepo { $repo_dir:
    ensure   => latest,
    provider => git,
    source   => 'https://github.com/Enucatl/dotfiles-vim.git',
    user     => $username,
    owner    => $username,
    group    => $username,
    require  => File[$home],
  }

  # Run rake links once after initial clone to set up symlinks.
  exec { 'dotfiles-rake-links':
    command     => 'rake links',
    cwd         => $repo_dir,
    user        => $username,
    path        => ['/usr/bin', '/bin', '/usr/local/bin'],
    environment => ["HOME=${home}"],
    refreshonly => true,
    subscribe   => Vcsrepo[$repo_dir],
    require     => Package['rake'],
  }
}
