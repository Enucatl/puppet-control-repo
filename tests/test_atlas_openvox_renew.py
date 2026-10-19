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
        "running": "true",
        "health": "healthy",
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
            'inspect --format {{.State.Running}} infra-openvox-1')
                [[ $failure != missing-container ]] || exit 42
                cat "$root/running"
                ;;
            'inspect --format {{.State.Running}} {{if .State.Health}}{{.State.Health.Status}}{{end}} infra-openvox-1')
                health=$(cat "$root/health")
                [[ $failure != wait ]] || health=starting
                printf '%s %s\n' "$(cat "$root/running")" "$health"
                if [[ $health == starting ]]; then printf healthy > "$root/health"; fi
                ;;
            'start infra-openvox-1')
                [[ $failure != start ]] || exit 42
                printf true > "$root/running"
                ;;
            'exec infra-openvox-1 /opt/puppetlabs/puppet/bin/ruby /infra/infra-renew.rb renew')
                [[ $(cat "$root/running") == true ]]
                [[ $failure != renew ]] || exit 42
                cat "$root/response"
                ;;
            'restart --time 60 infra-openvox-1')
                if [[ $failure == restart ]]; then printf false > "$root/running"; exit 42; fi
                ;;
            'exec infra-openvox-1 /opt/puppetlabs/puppet/bin/ruby /infra/infra-renew.rb verify')
                [[ $failure != verify ]] || exit 42
                printf '{"verified":true}\n'
                ;;
            *) exit 99 ;;
        esac
        ;;
    sleep) [[ $failure != wait ]] || exit 42 ;;
    *) exit 99 ;;
esac
"""
    )
    for name in ("id", "hostname", "flock", "docker", "sleep"):
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
    assert docker[0].endswith("inspect --format {{.State.Running}} infra-openvox-1")
    assert docker[1].endswith("/infra/infra-renew.rb renew")
    assert docker[-1].endswith("/infra/infra-renew.rb verify")
    assert len(docker) == (5 if reload_required else 4)
    if reload_required:
        assert docker[2].endswith("restart --time 60 infra-openvox-1")
        assert "{{.State.Health.Status}}" in docker[3]
    assert calls.index("flock -w 300 9") < calls.index(docker[0])


@pytest.mark.parametrize(
    "failure",
    ["renew", "restart", "wait", "verify", "json", "lock", "missing-container"],
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


def test_failed_restart_is_recovered_by_the_next_timer_invocation(
    renewal: Path,
) -> None:
    """Start the stopped existing container before retrying durable pending work."""
    (renewal / "failure").write_text("restart")
    result, _ = run_renewal(renewal)
    assert result.returncode != 0
    assert (renewal / "running").read_text() == "false"
    (renewal / "failure").write_text("")
    (renewal / "commands").unlink()

    result, calls = run_renewal(renewal)
    assert result.returncode == 0, result.stderr
    start = next(i for i, call in enumerate(calls) if " start infra-openvox-1" in call)
    renew = next(
        i for i, call in enumerate(calls) if call.endswith("infra-renew.rb renew")
    )
    assert start < renew
    assert calls[-1].endswith("infra-renew.rb verify")


def test_recovery_does_not_run_helper_if_existing_container_cannot_start(
    renewal: Path,
) -> None:
    """Preserve pending work when the established stopped container cannot start."""
    (renewal / "running").write_text("false")
    (renewal / "failure").write_text("start")
    result, calls = run_renewal(renewal)
    assert result.returncode != 0
    assert not any("infra-renew.rb" in call for call in calls)


def test_health_wait_allows_startup_and_has_a_deadline(renewal: Path) -> None:
    """Wait for the existing health check, then fail clearly when time expires."""
    (renewal / "health").write_text("starting")
    result, calls = run_renewal(renewal)
    assert result.returncode == 0, result.stderr
    assert "sleep 2" in calls

    wrapper = renewal / "renew.sh"
    wrapper.write_text(wrapper.read_text().replace("SECONDS + 300", "SECONDS + 0"))
    (renewal / "commands").unlink()
    result, calls = run_renewal(renewal)
    assert result.returncode != 0
    assert "Timed out waiting" in result.stderr
    assert not any("infra-renew.rb verify" in call for call in calls)


@pytest.mark.parametrize("health", ["unhealthy", ""])
def test_unavailable_health_does_not_verify_pending_work(
    renewal: Path, health: str
) -> None:
    """An absent or unhealthy service cannot complete certificate verification."""
    (renewal / "health").write_text(health)
    if health == "unhealthy":
        wrapper = renewal / "renew.sh"
        wrapper.write_text(wrapper.read_text().replace("SECONDS + 300", "SECONDS + 0"))
    result, calls = run_renewal(renewal)
    assert result.returncode != 0
    assert not any("infra-renew.rb verify" in call for call in calls)


def test_renewal_does_not_reconcile_checkout_configuration_or_image_drift(
    renewal: Path,
) -> None:
    """Only restart the existing container even when checkout deployment changes."""
    compose = renewal / "opt/docker/puppet-control-repo/docker/docker-compose.yml"
    compose.parent.mkdir(parents=True)
    compose.write_text(
        "services:\n  openvox:\n    image: openvox:unrelated-update\n"
        "    environment: {UNRELATED_SETTING: changed}\n"
    )
    result, calls = run_renewal(renewal)
    assert result.returncode == 0, result.stderr
    docker = [call for call in calls if call.startswith("docker ")]
    assert any(" restart --time 60 infra-openvox-1" in call for call in docker)
    assert not any(
        " compose " in call or " pull " in call or " create " in call for call in docker
    )


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
