# class neovim
#
# Manages installation of Neovim from GitHub releases. Downloads the latest
# linux-x86_64 tarball, extracts to /usr/local, and keeps it up to date.
#
# Example usage:
#   neovim::
#   neovim { 'version' => 'v0.10.0': }  # pin to specific version
#
class neovim (
  Optional[String[1]] $version = undef,
  String             $install_dir = '/usr/local',
) {
  # Constants
  $asset_name  = 'nvim-linux-x86_64.tar.gz'
  $marker_file = '/var/lib/neovim/version'

  # Install directories (not file paths)
  $install_bin   = "${install_dir}/bin"
  $install_lib   = "${install_dir}/lib/nvim"
  $install_share = "${install_dir}/share/nvim"

  # Ensure prerequisite directories
  file { ['/var/lib/neovim', $install_dir, $install_lib, $install_share]:
    ensure => directory,
    owner  => 'root',
    group  => 'root',
    mode   => '0755',
  }

  # Pass empty string for undef version so EPP template can handle it
  $version_str = $version ?? ''

  # Deploy installation script from EPP template
  file { '/usr/local/bin/install-neovim.sh':
    ensure  => file,
    content => epp('neovim/install-neovim.sh.epp', {
      'version'        => $version_str,
      'asset_name'     => $asset_name,
      'marker_file'    => $marker_file,
      'install_dir'    => $install_dir,
      'install_bin'    => $install_bin,
      'install_lib'    => $install_lib,
      'install_share'  => $install_share,
    }),
    mode    => '0755',
    owner   => 'root',
    group   => 'root',
    require => File['/var/lib/neovim'],
  }

  # Execute the installation script
  exec { 'neovim-install':
    command   => '/usr/local/bin/install-neovim.sh',
    path      => ['/usr/bin', '/bin', '/usr/local/bin', '/usr/sbin', '/sbin'],
    user      => 'root',
    cwd       => '/tmp',
    creates   => "${install_bin}/nvim",
    logoutput => true,
    require   => File['/usr/local/bin/install-neovim.sh'],
  }
}
