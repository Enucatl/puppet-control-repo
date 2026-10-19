"""Exercise host renewal orchestration without contacting Docker or systemd."""

from __future__ import annotations

import configparser
import json
import os
from pathlib import Path
import shlex
import subprocess

import pytest


PROXMOX = Path(__file__).resolve().parents[1] / "proxmox"


@pytest.fixture
def renewal(tmp_path: Path) -> Path:
    """Copy the wrapper into a fake host while using a real maintenance lock."""
    files = {
        "etc/openvox-infra/promoted": "openvox-authority:atlas.home.arpa\n",
        "root/openvox-infra/ready.json": "{}\n",
        "uid": "0",
        "hostname": "atlas.home.arpa",
        "failure": "",
        "response": '{"reload_required":true}',
    }
    for name, value in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
    (tmp_path / "run/lock").mkdir(parents=True)
    bins = tmp_path / "bin"
    bins.mkdir()
    command = (
        "#!/bin/bash\nset -eu\nroot="
        + shlex.quote(str(tmp_path))
        + "\n"
        + r"""
name=${0##*/}
printf '%s %s\n' "$name" "$*" >> "$root/commands"
failure=$(cat "$root/failure")
case "$name" in
    id) cat "$root/uid" ;;
    hostname) cat "$root/hostname" ;;
    flock)
        [[ "$*" == '-w 300 9' && $failure != lock ]] || exit 42
        exec /usr/bin/flock "$@"
        ;;
    docker)
        [[ $1 == --host && $2 == unix:///var/run/docker.sock ]]
        [[ -z ${DOCKER_HOST:-} && -z ${DOCKER_CONTEXT:-} ]]
        if /usr/bin/flock -n "$root/run/lock/atlas-openvox-maintenance.lock" true; then
            echo 'Maintenance lock was not held during renewal' >&2; exit 99
        fi
        shift 2
        case "$*" in
            'exec infra-openvox-1 /opt/puppetlabs/puppet/bin/ruby /infra/infra-renew.rb renew')
                [[ $failure != renew ]] || exit 42
                cat "$root/response"
                ;;
            'restart --time 60 infra-openvox-1')
                [[ $failure != restart ]] || exit 42
                ;;
            'compose --env-file /dev/null -p infra -f '*)
                [[ "$*" == *'docker/docker-compose.yml -f '* ]]
                [[ "$*" == *'docker/openvox/infra-vault.override.yaml up -d --wait --wait-timeout 300 --no-deps openvox' ]]
                [[ -z ${VAULT_TOKEN:-} && -z ${COMPOSE_FILE:-} && $DOCKER_DOMAIN == home.arpa ]]
                [[ $failure != wait ]] || exit 42
                ;;
            'exec infra-openvox-1 /opt/puppetlabs/puppet/bin/ruby /infra/infra-renew.rb verify')
                [[ $failure != verify ]] || exit 42
                printf '{"verified":true}\n'
                ;;
            *) exit 99 ;;
        esac
        ;;
    *) exit 99 ;;
esac
"""
    )
    for name in ("id", "hostname", "flock", "docker"):
        path = bins / name
        path.write_text(command)
        path.chmod(0o755)
    source = (PROXMOX / "atlas-openvox-renew.sh").read_text()
    for prefix in ("/etc/", "/root/", "/run/lock/", "/opt/docker/"):
        source = source.replace(prefix, str(tmp_path) + prefix)
    (tmp_path / "renew.sh").write_text(source)
    return tmp_path


def run_renewal(root: Path) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run host renewal with unrelated Docker and Compose ambient settings."""
    result = subprocess.run(
        ["bash", str(root / "renew.sh")],
        env={
            **os.environ,
            "PATH": f"{root / 'bin'}:{os.environ['PATH']}",
            "DOCKER_HOST": "tcp://wrong-host:2375",
            "DOCKER_CONTEXT": "unrelated-context",
            "COMPOSE_FILE": "/unrelated/docker-compose.yml",
            "VAULT_TOKEN": "must-not-reach-compose",
        },
        text=True,
        capture_output=True,
        timeout=5,
    )
    log = root / "commands"
    return result, log.read_text().splitlines() if log.exists() else []


@pytest.mark.parametrize("reload_required", [False, True])
def test_renewal_restarts_only_when_required_and_verifies_after_readiness(
    renewal: Path, reload_required: bool
) -> None:
    """Protect backup state and reload only the server whose certificate changed."""
    (renewal / "response").write_text(json.dumps({"reload_required": reload_required}))
    result, calls = run_renewal(renewal)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"verified": True}
    docker = [call for call in calls if call.startswith("docker ")]
    assert docker[0].endswith("/infra/infra-renew.rb renew")
    assert docker[-1].endswith("/infra/infra-renew.rb verify")
    assert len(docker) == (4 if reload_required else 2)
    if reload_required:
        assert docker[1].endswith("restart --time 60 infra-openvox-1")
        assert " up -d --wait --wait-timeout 300 --no-deps openvox" in docker[2]
    assert calls.index("flock -w 300 9") < calls.index(docker[0])


@pytest.mark.parametrize(
    "failure", ["renew", "restart", "wait", "verify", "json", "lock"]
)
def test_renewal_failure_propagates_without_clearing_pending_work(
    renewal: Path, failure: str
) -> None:
    """Leave verification to a later retry if issuance, restart or readiness fails."""
    if failure == "json":
        (renewal / "response").write_text('{"reload_required":"yes"}')
    else:
        (renewal / "failure").write_text(failure)
    result, calls = run_renewal(renewal)
    assert result.returncode != 0
    if failure != "verify":
        assert not any(call.endswith("/infra/infra-renew.rb verify") for call in calls)
    if failure in ("renew", "json", "lock"):
        assert not any(" restart " in call for call in calls)


@pytest.mark.parametrize("failure", ["root", "hostname", "promoted", "ready"])
def test_renewal_requires_established_atlas_before_docker(
    renewal: Path, failure: str
) -> None:
    """Reject authority mismatches without starting a Docker operation."""
    if failure == "root":
        (renewal / "uid").write_text("1000")
    elif failure == "hostname":
        (renewal / "hostname").write_text("docker.home.arpa")
    elif failure == "promoted":
        (renewal / "etc/openvox-infra/promoted").write_text("development")
    else:
        (renewal / "root/openvox-infra/ready.json").unlink()
    result, calls = run_renewal(renewal)
    assert result.returncode != 0
    assert not any(call.startswith(("docker ", "flock ")) for call in calls)


def test_systemd_units_define_bounded_root_renewal_and_six_hour_schedule() -> None:
    """Validate deployment entrypoint, service ordering and renewal cadence."""
    service = configparser.ConfigParser()
    service.read(PROXMOX / "systemd/atlas-openvox-renew.service")
    assert service["Unit"]["Requires"] == "docker.service"
    assert service["Unit"]["After"] == "docker.service"
    assert service["Service"]["Type"] == "oneshot"
    assert service["Service"]["User"] == "root"
    assert service["Service"]["ExecStart"] == (
        "/opt/docker/puppet-control-repo/proxmox/atlas-openvox-renew.sh"
    )
    assert (
        int(service["Service"]["TimeoutStartSec"].removesuffix("min")) * 60
        > 300 + 60 + 300
    )
    timer = configparser.ConfigParser()
    timer.read(PROXMOX / "systemd/atlas-openvox-renew.timer")
    assert timer["Timer"]["OnBootSec"] == "5min"
    assert timer["Timer"]["OnUnitActiveSec"] == "6h"
    assert timer["Timer"]["Unit"] == "atlas-openvox-renew.service"
    assert timer["Install"]["WantedBy"] == "timers.target"
