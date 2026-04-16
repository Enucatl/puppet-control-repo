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
  String            $install_dir = '/usr/local',
) {
  $asset_name = 'nvim-linux-x86_64.tar.gz'

  # Version tracking file for idempotency
  $marker_file = '/var/lib/neovim/version'

  # Install directory structure
  $install_base = "${install_dir}/nvim"
  $install_bin  = "${install_dir}/bin/nvim"
  $install_lib  = "${install_dir}/lib/nvim"
  $install_share = "${install_dir}/share/nvim"

  # Ensure parent directories exist
  file { '/var/lib/neovim':
    ensure => directory,
    owner  => 'root',
    group  => 'root',
    mode   => '0755',
  }

  # Download and install Neovim
  exec { 'neovim-install':
    command     => "/bin/bash -c '
      if [[ -n \"${version}\" ]]; then
        TAG=\"${version}\";
        URL=\"https://github.com/neovim/neovim/releases/download/${version}/nvim-linux-x86_64.tar.gz\";
      else
        TAG=$(curl -sL https://api.github.com/repos/neovim/neovim/releases/latest | jq -r .tag_name);
        URL=$(curl -sL https://api.github.com/repos/neovim/neovim/releases/latest | jq -r .assets[] | select(.name | contains(\"nvim-linux-x86_64\")) | .browser_download_url);
      fi;
      INSTALL_BASE=\"${install_base}\";
      MARKER=\"${marker_file}\";
      ASSET=\"${asset_name}\";
      if [ ! -f \"/tmp/\${ASSET}\" ] || [ ! -f \"\${MARKER}\" ] || [ \"\$(cat \${MARKER} 2>/dev/null)\" != \"\${TAG}\" ]; then
        curl -sL \"\${URL}\" -o \"/tmp/\${ASSET}\";
        tar xzf \"/tmp/\${ASSET}\" -C /tmp;
        rm -rf \"${install_dir}/nvim\";
        cp -a /tmp/nvim-linux-x86_64/bin \"${install_bin}\";
        cp -a /tmp/nvim-linux-x86_64/lib \"${install_lib}\";
        cp -a /tmp/nvim-linux-x86_64/share \"${install_share}\";
        echo \"\${TAG}\" > \"\${MARKER}\";
      fi'
    ",
    cwd         => '/tmp',
    user        => 'root',
    path        => ['/usr/bin', '/bin', '/usr/local/bin'],
    creates     => "${install_bin}/nvim",
    refresh     => true,
    refreshonly => true,
  }
}
