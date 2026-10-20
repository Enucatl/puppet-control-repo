"""Verify service startup still coordinates with maintenance operations."""

from __future__ import annotations

import fcntl
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_start_waits_for_backup_and_releases_lock_before_renewal(
    tmp_path: Path,
) -> None:
    """Exclude service startup during backup and let synchronous renewal take its lock."""
    lock = tmp_path / "maintenance.lock"
    (tmp_path / "ready.json").write_text("{}")
    program = """
import importlib.util, pathlib, subprocess, sys
spec = importlib.util.spec_from_file_location('infra', sys.argv[1])
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)
controller.STATE = pathlib.Path(sys.argv[2])
controller.MAINTENANCE_LOCK = controller.STATE / 'maintenance.lock'
def compose(*arguments):
    assert subprocess.run(['flock', '-n', str(controller.MAINTENANCE_LOCK), 'true']).returncode != 0
    (controller.STATE / 'started').touch()
def run(arguments):
    if arguments[0] == 'install' or arguments == ['systemctl', 'daemon-reload']:
        assert subprocess.run(['flock', '-n', str(controller.MAINTENANCE_LOCK), 'true']).returncode != 0
    if arguments == ['systemctl', 'start', 'atlas-openvox-renew.service']:
        subprocess.run(['flock', '-n', str(controller.MAINTENANCE_LOCK), 'true'], check=True)
        (controller.STATE / 'renewed').touch()
controller.compose, controller.run = compose, run
print('waiting', flush=True)
controller.start()
"""
    with lock.open("a") as backup:
        fcntl.flock(backup, fcntl.LOCK_EX)
        with subprocess.Popen(
            [
                sys.executable,
                "-c",
                program,
                str(ROOT / "proxmox/atlas-infra.py"),
                str(tmp_path),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ) as startup:
            try:
                assert startup.stdout and startup.stdout.readline() == "waiting\n"
                with pytest.raises(subprocess.TimeoutExpired):
                    startup.communicate(timeout=0.2)
                assert not (tmp_path / "started").exists()
                fcntl.flock(backup, fcntl.LOCK_UN)
                _, error = startup.communicate(timeout=5)
                assert startup.returncode == 0, error
            finally:
                if startup.poll() is None:
                    startup.kill()
    assert (tmp_path / "started").exists()
    assert (tmp_path / "renewed").exists()
