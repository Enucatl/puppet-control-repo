"""Verify maintenance exclusion with real Git receivers and process-held locks."""

from __future__ import annotations

import fcntl
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def git(directory: Path, *arguments: str) -> str:
    """Run Git against an isolated repository."""
    return subprocess.check_output(
        ["git", "-C", str(directory), *arguments], text=True, stderr=subprocess.PIPE
    ).strip()


@pytest.fixture
def receiver(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    """Create a receiver guarded by the actual host hook and a publishable commit."""
    source, bare, lock = tmp_path / "source", tmp_path / "bare", tmp_path / "lock"
    source.mkdir()
    git(source, "init", "-q")
    git(
        source,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@invalid",
        "commit",
        "--allow-empty",
        "-qm",
        "Initial",
    )
    git(source, "clone", "--bare", str(source), str(bare))
    git(
        source,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@invalid",
        "commit",
        "--allow-empty",
        "-qm",
        "Published",
    )
    hook = bare / "hooks/pre-receive"
    hook.write_text(
        (ROOT / "docker/openvox/infra-host-pre-receive")
        .read_text()
        .replace("/run/lock/atlas-openvox-maintenance.lock", str(lock))
    )
    hook.chmod(0o755)
    return source, bare, lock, git(source, "rev-parse", "HEAD")


@pytest.mark.parametrize("descriptor", ["absent", "unrelated"])
def test_uncoordinated_receive_cannot_change_refs(
    receiver: tuple[Path, Path, Path, str], descriptor: str
) -> None:
    """Reject direct pushes, including callers holding a different lock on FD 9."""
    source, bare, lock, revision = receiver
    before = git(bare, "show-ref")
    script = 'git -C "$1" push "$2" HEAD:refs/heads/production'
    if descriptor == "unrelated":
        script = 'exec 9>"$3"; flock 9; ' + script
    result = subprocess.run(
        ["bash", "-c", script, "receive", str(source), str(bare), str(lock) + ".other"],
        text=True,
        capture_output=True,
        timeout=5,
    )
    assert result.returncode != 0
    assert "use atlas-publish.sh" in result.stderr
    assert git(bare, "show-ref") == before
    assert revision not in git(bare, "show-ref")


def test_receive_waits_for_backup_then_inherits_lock(
    receiver: tuple[Path, Path, Path, str],
) -> None:
    """Hold publication before refs change, then retain FD 9 through receive-pack."""
    source, bare, lock, revision = receiver
    before = git(bare, "show-ref")
    with lock.open("a") as backup:
        fcntl.flock(backup, fcntl.LOCK_EX)
        with subprocess.Popen(
            [
                "bash",
                "-c",
                'exec 9>"$3"; echo waiting; flock 9; '
                'git -C "$1" push "$2" HEAD:refs/heads/production',
                "publish",
                str(source),
                str(bare),
                str(lock),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ) as publisher:
            try:
                assert publisher.stdout and publisher.stdout.readline() == "waiting\n"
                with pytest.raises(subprocess.TimeoutExpired):
                    publisher.communicate(timeout=0.2)
                assert git(bare, "show-ref") == before
                fcntl.flock(backup, fcntl.LOCK_UN)
                _, error = publisher.communicate(timeout=5)
                assert publisher.returncode == 0, error
            finally:
                if publisher.poll() is None:
                    publisher.kill()
    assert git(bare, "rev-parse", "refs/heads/production") == revision


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
