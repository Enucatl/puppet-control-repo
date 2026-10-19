"""Run Atlas bootstrap against isolated files, real Git and harmless host commands."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "proxmox/atlas-bootstrap.sh"
FINGERPRINT = "9DC858229FC7DD38854AE2D88D81803C0EBFCD88"


def git(directory: Path, *arguments: str) -> str:
    """Run real Git against a disposable repository."""
    return subprocess.check_output(
        ["git", "-C", str(directory), *arguments], text=True, stderr=subprocess.PIPE
    ).strip()


@pytest.fixture
def bootstrap(tmp_path: Path) -> Path:
    """Copy the script into a fake host with local Git origins and command doubles."""
    for directory in ("etc/apt/sources.list.d", "bin", "home", "upstream"):
        (tmp_path / directory).mkdir(parents=True)
    (tmp_path / "etc/os-release").write_text("ID=ubuntu\nVERSION_ID=26.04\n")
    for mapping in ("subuid", "subgid"):
        (tmp_path / "etc" / mapping).write_text("user_l:100000:65536\n")
    (tmp_path / "hostname").write_text("atlas.home.arpa\n")
    (tmp_path / "architecture").write_text("amd64\n")
    (tmp_path / "failure").write_text("")
    (tmp_path / "fingerprint").write_text(FINGERPRINT + "\n")
    revisions = {}
    for name in ("puppet-control-repo", "docker-compose-security-baseline"):
        repository = tmp_path / "upstream" / name
        repository.mkdir()
        git(repository, "init", "-q")
        (repository / "fixture").write_text(name)
        git(repository, "add", ".")
        git(
            repository,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "Fixture",
        )
        revisions[name] = git(repository, "rev-parse", "HEAD")
    (tmp_path / "revision").write_text(revisions["puppet-control-repo"])
    source = SCRIPT.read_text()
    for name in revisions:
        source = source.replace(
            f"git@github.com:Enucatl/{name}.git", str(tmp_path / "upstream" / name)
        )
    source = source.replace(
        "41c57257d33d471e2ae7cc7264938944c67434fd",
        revisions["docker-compose-security-baseline"],
    )
    for prefix in ("/etc/", "/opt/", "/var/"):
        source = source.replace(prefix, str(tmp_path) + prefix)
    (tmp_path / "bootstrap.sh").write_text(source)
    command = (
        f"#!/bin/bash\nset -eu\nroot={shlex.quote(str(tmp_path))}\n"
        + r"""
name=${0##*/}
printf '%s %s\n' "$name" "$*" >> "$root/commands"
failure=$(cat "$root/failure")
case "$name" in
  id) echo 0 ;;
  hostname) cat "$root/hostname" ;;
  dpkg) cat "$root/architecture" ;;
  apt-get)
    if [[ $failure == packages && "$*" == *docker-ce=* ]]; then exit 42; fi
    ;;
  curl) printf 'fixture key\n' > "${@: -1}" ;;
  gpg)
    printf 'pub:::::::::\nfpr:::::::::%s:\n' "$(cat "$root/fingerprint")"
    ;;
  docker)
    if [[ " $* " == *' info '* ]]; then
      [[ $1 == --host && $2 == "unix://$root/var/run/docker.sock" ]]
      shift 2
      [[ $1 == info ]]
      printf '{"DockerRootDir":"%s/var/lib/docker/100000.100000","SecurityOptions":["name=userns"],"ServerVersion":"29.9.1","Driver":"overlayfs"}\n' "$root"
    elif [[ "$*" == *'config --quiet'* ]]; then
      [[ -z ${VAULT_TOKEN:-} && -z ${COMPOSE_FILE:-} && -z ${DOCKER_HOST:-} && -z ${DOCKER_CONTEXT:-} ]]
      [[ $DOCKER_DOMAIN == atlas.home.arpa && "$*" == *'--env-file /dev/null'* ]]
      [[ $failure != compose ]] || exit 42
    fi
    ;;
  dpkg-query) printf 'docker-ce\tfixture-version\n' ;;
  systemctl) : ;;
  *) exit 99 ;;
esac
"""
    )
    for name in (
        "id",
        "hostname",
        "dpkg",
        "apt-get",
        "curl",
        "gpg",
        "docker",
        "dpkg-query",
        "systemctl",
    ):
        binary = tmp_path / "bin" / name
        binary.write_text(command)
        binary.chmod(0o755)
    return tmp_path


def run_bootstrap(
    root: Path, revision: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Execute the copied script with network/package operations replaced."""
    return subprocess.run(
        [
            "bash",
            str(root / "bootstrap.sh"),
            revision if revision is not None else (root / "revision").read_text(),
        ],
        env={
            "PATH": f"{root / 'bin'}:{os.environ['PATH']}",
            "HOME": str(root / "home"),
            "VAULT_TOKEN": "must-not-reach-compose",
            "COMPOSE_FILE": "/unrelated/compose.yml",
            "DOCKER_HOST": "tcp://old-host:2375",
            "DOCKER_CONTEXT": "old-host",
        },
        text=True,
        capture_output=True,
        timeout=15,
    )


def test_fresh_and_repeated_bootstrap(bootstrap: Path) -> None:
    """Install once, retain checkouts/settings on rerun and validate sanitized Compose."""
    for _ in range(2):
        result = run_bootstrap(bootstrap)
        assert result.returncode == 0, result.stderr
        checkout = bootstrap / "opt/docker/puppet-control-repo"
        assert (
            git(checkout, "rev-parse", "HEAD") == (bootstrap / "revision").read_text()
        )
        assert git(checkout, "status", "--porcelain") == ""
        evidence = json.loads(
            (
                bootstrap / "var/log/openvox-provisioning/atlas-bootstrap.json"
            ).read_text()
        )
        assert evidence["control_revision"] == git(checkout, "rev-parse", "HEAD")
        assert evidence["userns_base"] == 100000
    assert len(list((bootstrap / "etc/apt/sources.list.d").iterdir())) == 1
    assert (
        json.loads((bootstrap / "etc/docker/daemon.json").read_text())["userns-remap"]
        == "user_l"
    )
    calls = (bootstrap / "commands").read_text()
    assert calls.count("systemctl enable --now docker\n") == 2
    assert "config --quiet" in calls
    assert "compose up" not in calls


@pytest.mark.parametrize(
    ("file", "value"),
    [
        ("hostname", "docker.home.arpa"),
        ("etc/os-release", "ID=ubuntu\nVERSION_ID=24.04\n"),
        ("architecture", "arm64"),
        ("etc/subuid", "user_l:165536:65536\n"),
        ("etc/subgid", "user_l:100000:65536\nuser_l:165536:65536\n"),
    ],
)
def test_wrong_target_refused_before_packages(
    bootstrap: Path, file: str, value: str
) -> None:
    """Reject hosts or ownership mappings outside the declared Atlas prerequisites."""
    (bootstrap / file).write_text(value)
    assert run_bootstrap(bootstrap).returncode != 0
    assert "apt-get" not in (bootstrap / "commands").read_text()
    assert not (bootstrap / "opt/docker").exists()


@pytest.mark.parametrize("revision", ["dev", "-bad", "a" * 39, "f" * 40])
def test_invalid_or_missing_commit_refused(bootstrap: Path, revision: str) -> None:
    """Require a real full commit and never enable Docker after failed fetches."""
    assert run_bootstrap(bootstrap, revision).returncode != 0
    assert not (bootstrap / "etc/docker/daemon.json").exists()


@pytest.mark.parametrize("state", ["different-settings", "unconfigured-state"])
def test_existing_docker_state_is_preserved(bootstrap: Path, state: str) -> None:
    """Do not remap an existing daemon or silently adopt its stored containers."""
    daemon = bootstrap / "etc/docker/daemon.json"
    if state == "different-settings":
        daemon.parent.mkdir()
        daemon.write_text('{"userns-remap":"default"}\n')
    else:
        (bootstrap / "var/lib/docker").mkdir(parents=True)
    assert run_bootstrap(bootstrap).returncode != 0
    assert "systemctl" not in (bootstrap / "commands").read_text()
    if daemon.exists():
        assert json.loads(daemon.read_text()) == {"userns-remap": "default"}


@pytest.mark.parametrize("problem", ["dirty", "wrong-origin"])
def test_modified_checkout_is_preserved(bootstrap: Path, problem: str) -> None:
    """Refuse unexpected existing repositories rather than resetting user work."""
    assert run_bootstrap(bootstrap).returncode == 0
    checkout = bootstrap / "opt/docker/puppet-control-repo"
    if problem == "dirty":
        (checkout / "fixture").write_text("operator change")
    else:
        git(checkout, "remote", "set-url", "origin", "/unrelated/repository")
    result = run_bootstrap(bootstrap)
    assert result.returncode != 0
    assert "Refusing to overwrite" in result.stderr
    if problem == "dirty":
        assert (checkout / "fixture").read_text() == "operator change"
    else:
        assert git(checkout, "remote", "get-url", "origin") == "/unrelated/repository"


@pytest.mark.parametrize("failure", ["packages", "fingerprint", "compose"])
def test_failed_setup_never_records_success(bootstrap: Path, failure: str) -> None:
    """Propagate package, trust and Compose failures without success evidence."""
    (bootstrap / "failure").write_text(failure)
    if failure == "fingerprint":
        (bootstrap / "fingerprint").write_text("A" * 40)
    assert run_bootstrap(bootstrap).returncode != 0
    assert not (
        bootstrap / "var/log/openvox-provisioning/atlas-bootstrap.json"
    ).exists()
    if failure in ("packages", "fingerprint"):
        assert "systemctl" not in (bootstrap / "commands").read_text()
