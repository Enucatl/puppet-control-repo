# neovim
#
# Installs Neovim from GitHub releases on Debian-based systems.
#
# ## Behavior
#
# Downloads the `nvim-linux-x86_64.tar.gz` asset from the latest release, extracts
# it to `/usr/local`, and installs runtime data under `/usr/local/lib/nvim`
# and `/usr/local/share/nvim`.
#
# ## Usage
#
# ### Install latest version
#
# ```puppet
# include neovim
# ```
#
# ### Pin to specific version
#
# ```puppet
# neovim { 'version' => 'v0.10.0': }
# ```
#
# ## How it works
#
# - Uses GitHub API (`/releases/latest`) to fetch version info (~200 bytes response)
# - Stores version in `/var/lib/neovim/version` marker file
# - Only downloads and installs when:
#   - Marker file doesn't exist (first install)
#   - Version changed (new release available)
# - Idempotent: subsequent runs check version without downloading unless updated
#
# ## Tradeoffs
#
# - **Lightweight checking**: Single JSON API call vs. downloading 50MB tarball each run
# - **Freshness**: Always installs latest unless version is pinned
# - **Network**: ~200 bytes for version check, ~50MB only on new release
# - **CPU**: Minimal; just curl + grep for version comparison
#
# ## Files managed
#
# - `/usr/local/bin/nvim` -> Neovim binary
# - `/usr/local/lib/nvim/*` -> runtime libraries and parsers
# - `/usr/local/share/nvim/*` -> runtime assets and syntax files
# - `/var/lib/neovim/version` -> version marker file
