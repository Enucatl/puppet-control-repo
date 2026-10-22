"""Verify Steam deduplication fails closed before touching a foreign filesystem."""

import os
import subprocess
from pathlib import Path

import pytest

from puppet_catalog import DEPENDENCIES, ROOT, compile_catalog, resources


@pytest.mark.parametrize("filesystem", ["ext4", "btrfs"])
def test_refuse_non_xfs(tmp_path: Path, filesystem: str) -> None:
    """Reject unsupported roots before attempting to hash production game data."""
    findmnt = tmp_path / "findmnt"
    findmnt.write_text(f"#!/bin/sh\necho {filesystem}\n")
    findmnt.chmod(0o755)
    result = subprocess.run(
        ["sh", str(ROOT / "modules/profile/files/steam-dedup")],
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "/ must be on the XFS root filesystem" in result.stderr


def test_resource_limits_and_monthly_timer(tmp_path: Path) -> None:
    """Compile a timer that bounds deduplication and reports its peak resource use."""
    if not (DEPENDENCIES / "systemd/manifests/unit_file.pp").is_file():
        pytest.skip("Set PUPPET_TEST_MODULEPATH to the installed Forge dependencies")
    catalog = resources(
        compile_catalog(tmp_path, "service { 'alloy': } include profile::steam_dedup")
    )
    service = catalog["File[/etc/systemd/system/steam-dedup.service]"]["content"]
    assert "MemoryMax=2G" in service
    assert "MemorySwapMax=0" in service
    assert "CPUQuota=50%" in service
    assert "IOSchedulingClass=idle" in service
    assert "ExecStart=/usr/bin/time -v /usr/local/sbin/steam-dedup" in service
    assert "Type=oneshot" in service
    timer = catalog["File[/etc/systemd/system/steam-dedup.timer]"]["content"]
    assert "OnCalendar=*-*-01 04:00:00 UTC" in timer
    assert "Unit=steam-dedup.service" in timer
    assert catalog["Systemd::Unit_file[steam-dedup.timer]"]["active"] is True
