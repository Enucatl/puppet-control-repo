# Neovim

Installs the GitHub `nvim-linux-x86_64.tar.gz` release under `/usr/local`.
Include `neovim` for the latest release, or pin a release:

```puppet
class { 'neovim':
  version => 'v0.10.0',
}
```

Every Puppet run compares the requested release with `/var/lib/neovim/version`.
Unpinned installs fetch release metadata once per run. The archive is downloaded
only when the release differs or the binary is missing. Downloads and extraction
use a private temporary directory that is removed on success or failure.

The binary lives at `/usr/local/bin/nvim`, with libraries and runtime data under
`/usr/local/lib/nvim` and `/usr/local/share/nvim`. The version marker is written
only after installation succeeds.
