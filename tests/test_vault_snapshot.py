"""Exercise Vault snapshot recovery using private fixtures and real tar archives."""

from __future__ import annotations

import os
from pathlib import Path
import shlex
import subprocess
import tarfile

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "docker/vault/scripts/backup.sh"
STATE = "var/lib/docker/100000.100000/volumes/infra_vault_data/_data"


@pytest.fixture
def authority(tmp_path: Path) -> Path:
    """Replace host operations while retaining archive and filesystem behavior."""
    files = {
        STATE + "/core/state": "PRIVATE-VAULT-STATE-CANARY\n",
        "uid": "0",
        "hostname": "atlas.home.arpa",
        "failure": "",
    }
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    (tmp_path / "run/lock").mkdir(parents=True)
    bins = tmp_path / "bin"
    bins.mkdir()
    mock = (
        "#!/bin/bash\nset -euo pipefail\nroot="
        + shlex.quote(str(tmp_path))
        + "\n"
        + r"""
name=${0##*/}
printf '%s %s\n' "$name" "$*" >> "$root/commands"
failure=$(cat "$root/failure")
case "$name" in
  id) cat "$root/uid" ;;
  hostname) cat "$root/hostname" ;;
  date) echo '2026-10-21T12:00:00+00:00' ;;
  flock)
    [[ $failure != lock ]]
    exec /usr/bin/flock "$@"
    ;;
  install)
    [[ $* == '-d -o root -g backrest -m 0750 '* ]]
    exec /usr/bin/install -d -m 0750 "${@: -1}"
    ;;
  chgrp) [[ $1 == backrest && -f $2 ]] ;;
  logger) printf '%s\n' "${@: -1}" >&2 ;;
  sleep) ;;
  tar)
    [[ -f $root/stopped ]]
    if [[ $failure == archive && $* == *' -czf '* ]]; then
      printf incomplete > "$5"
      exit 42
    fi
    [[ $failure != validation || $1 != -tzf ]] || exit 42
    exec /usr/bin/tar "$@"
    ;;
  docker)
    [[ -z ${DOCKER_HOST:-} && -z ${DOCKER_CONTEXT:-} ]]
    case "$1 $2" in
      'compose --env-file')
        [[ -z ${VAULT_TOKEN:-} && -z ${COMPOSE_FILE:-} ]]
        [[ $DOCKER_DOMAIN == home.arpa ]]
        [[ $* == *'--env-file /dev/null -p infra -f '*'/compose.yaml '* ]]
        case "$*" in
          *' stop vault-unsealer vault')
            touch "$root/stopped"
            [[ $failure != stop ]]
            ;;
          *' start vault vault-unsealer')
            [[ $failure != restart ]]
            rm -f "$root/stopped"
            ;;
          *) exit 99 ;;
        esac
        ;;
      'inspect --format')
        [[ ${@: -2} == 'infra-vault-1 infra-vault-unsealer-1' && ! -f $root/stopped ]]
        if [[ $failure == unhealthy ]]; then printf 'healthy\nunhealthy\n'; else printf 'healthy\nhealthy\n'; fi
        ;;
      *) exit 99 ;;
    esac
    ;;
  *) exit 99 ;;
esac
"""
    )
    for name in (
        "id",
        "hostname",
        "date",
        "flock",
        "install",
        "chgrp",
        "logger",
        "sleep",
        "tar",
        "docker",
    ):
        path = bins / name
        path.write_text(mock)
        path.chmod(0o755)
    source = SCRIPT.read_text()
    for prefix in (
        "/run/lock",
        "/var/lib/docker",
        "/scratch",
        "/opt/docker",
    ):
        source = source.replace(prefix, str(tmp_path) + prefix)
    (tmp_path / "backup.sh").write_text(source)
    return tmp_path


def snapshot(root: Path) -> subprocess.CompletedProcess[str]:
    """Run without letting ambient credentials or remote Docker settings escape."""
    return subprocess.run(
        ["bash", str(root / "backup.sh")],
        env={
            **os.environ,
            "PATH": f"{root / 'bin'}:{os.environ['PATH']}",
            "VAULT_TOKEN": "MUST-NOT-REACH-COMPOSE",
            "COMPOSE_FILE": "/unrelated/compose.yml",
            "DOCKER_HOST": "tcp://unrelated:2375",
            "DOCKER_CONTEXT": "unrelated",
        },
        text=True,
        capture_output=True,
        timeout=10,
    )


def test_snapshot_preserves_private_state_and_backrest_access(authority: Path) -> None:
    """Export native storage without promotion markers or custom volume labels."""
    result = snapshot(authority)
    assert result.returncode == 0, result.stderr
    directory = authority / "scratch/backup/vault"
    archives = list(directory.glob("vault-backup-*.tar.gz"))
    assert len(archives) == 1
    archive = archives[0]
    assert directory.stat().st_mode & 0o777 == 0o750
    assert archive.stat().st_mode & 0o777 == 0o640
    with tarfile.open(archive) as backup:
        member = backup.getmember("./core/state")
        assert backup.extractfile(member).read() == b"PRIVATE-VAULT-STATE-CANARY\n"
        source = (authority / STATE / "core/state").stat()
        assert (member.uid, member.gid) == (source.st_uid, source.st_gid)
    commands = (authority / "commands").read_text()
    assert "volume inspect" not in commands
    assert commands.count(" stop vault-unsealer vault\n") == 1
    assert commands.count(" start vault vault-unsealer\n") == 1
    assert f"chgrp backrest {archive}\n" in commands
    assert commands.index(" stop ") < commands.index("tar --acls")
    assert commands.index("tar -tzf") < commands.index(" start ")
    assert "State.Health.Status" in commands
    assert not (authority / "stopped").exists()
    assert "PRIVATE-VAULT-STATE-CANARY" not in result.stdout + result.stderr
    assert "MUST-NOT-REACH-COMPOSE" not in result.stdout + result.stderr


@pytest.mark.parametrize("failure", ["archive", "validation", "stop"])
def test_failed_snapshot_restarts_vault_and_discards_partial_archive(
    authority: Path, failure: str
) -> None:
    """Restore both services after interrupted stopping or invalid archive output."""
    (authority / "failure").write_text(failure)
    result = snapshot(authority)
    assert result.returncode != 0
    commands = (authority / "commands").read_text()
    assert " start vault vault-unsealer\n" in commands
    assert not (authority / "stopped").exists()
    assert not list((authority / "scratch/backup/vault").glob("*.tar.gz"))
    assert "SUCCESS:" not in result.stdout + result.stderr


@pytest.mark.parametrize("failure", ["restart", "unhealthy"])
def test_snapshot_cannot_report_success_before_vault_recovers(
    authority: Path, failure: str
) -> None:
    """Retain a completed archive but fail when restored Vault cannot serve again."""
    (authority / "failure").write_text(failure)
    result = snapshot(authority)
    assert result.returncode != 0
    assert "SUCCESS:" not in result.stdout + result.stderr
    assert len(list((authority / "scratch/backup/vault").glob("*.tar.gz"))) == 1


@pytest.mark.parametrize(
    "failure",
    [
        "root",
        "hostname",
        "missing-source",
        "lock",
        "existing",
    ],
)
def test_snapshot_refuses_missing_authority_before_stopping(
    authority: Path, failure: str
) -> None:
    """Never interrupt services without local privilege and original authority state."""
    if failure == "root":
        (authority / "uid").write_text("1000")
    elif failure == "hostname":
        (authority / "hostname").write_text("docker.home.arpa")
    elif failure == "missing-source":
        (authority / STATE).rename(authority / "retained-state")
    elif failure == "existing":
        destination = (
            authority
            / "scratch/backup/vault/vault-backup-2026-10-21T12:00:00+00:00.tar.gz"
        )
        destination.parent.mkdir(parents=True)
        destination.write_text("EXISTING-ARCHIVE-CANARY")
    else:
        (authority / "failure").write_text(failure)
    result = snapshot(authority)
    assert result.returncode != 0
    assert " stop " not in (authority / "commands").read_text()
    assert not (authority / "stopped").exists()
    if failure == "existing":
        assert destination.read_text() == "EXISTING-ARCHIVE-CANARY"
