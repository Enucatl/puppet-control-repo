"""Exercise Node first installation without downloading or changing real tools."""

import os
from pathlib import Path
import subprocess

import pytest

from puppet_catalog import compile_catalog, resources


@pytest.mark.parametrize("installed", [False, True])
def test_nvm_missing_node_is_installed_without_treating_absence_as_failure(
    tmp_path: Path, installed: bool
) -> None:
    """An N/A status from NVM is expected until the first Node version exists."""
    catalog = compile_catalog(
        tmp_path / "catalog",
        """
      class { 'profile::user_toolchain':
        manage_uv => false, manage_rustup => false, npm_globals => [],
      }
    """,
    )
    script = resources(catalog)["File[/usr/local/sbin/puppet-user-toolchain-sync]"][
        "content"
    ]
    home = tmp_path / "home"
    nvm = home / ".nvm"
    (nvm / ".git").mkdir(parents=True)
    if installed:
        (nvm / "installed").touch()
    (nvm / "nvm.sh").write_text("""
nvm() {
    case "$1" in
        version)
            if [[ -f $NVM_DIR/installed ]]; then echo v26.12.0
            else echo N/A; return 3; fi ;;
        version-remote) echo v26.12.0 ;;
        install) touch "$NVM_DIR/installed" "$NVM_DIR/install-called" ;;
        alias|use) : ;;
        *) return 99 ;;
    esac
}
""")
    binary = home / ".local/bin"
    binary.mkdir(parents=True)
    for name, body in {
        "git": "echo v0.40.8",
        "curl": 'echo \'{"tag_name":"v0.40.8"}\'',
        "npm": 'case "$1" in --version|view) echo 12.0.0 ;; config) : ;; *) exit 99 ;; esac',
    }.items():
        tool = binary / name
        tool.write_text("#!/bin/bash\nset -eu\n" + body + "\n")
        tool.chmod(0o755)
    script = script.replace("/home/user", str(home)).replace(
        "/usr/local/sbin/puppet-toolchain-versions-prune", "/bin/true"
    )
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=home,
        env=os.environ,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (nvm / "installed").exists()
    assert (nvm / "install-called").exists() == (not installed)
