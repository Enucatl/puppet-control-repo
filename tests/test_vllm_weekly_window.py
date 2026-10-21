from __future__ import annotations

import dataclasses
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


FILES_DIR = Path(__file__).parents[1] / "modules" / "proxmox_workflows" / "files"
SHARED_SPEC = importlib.util.spec_from_file_location(
    "proxmox_orchestration", FILES_DIR / "proxmox_orchestration.py"
)
assert SHARED_SPEC is not None
shared = importlib.util.module_from_spec(SHARED_SPEC)
sys.modules["proxmox_orchestration"] = shared
assert SHARED_SPEC.loader is not None
SHARED_SPEC.loader.exec_module(shared)

SPEC = importlib.util.spec_from_file_location(
    "vllm_weekly_window", FILES_DIR / "vllm_weekly_window.py"
)
assert SPEC is not None
vllm = importlib.util.module_from_spec(SPEC)
sys.modules["vllm_weekly_window"] = vllm
assert SPEC.loader is not None
SPEC.loader.exec_module(vllm)


class FakeRunner:
    def __init__(self, responses: list[tuple[str, int, str]]) -> None:
        self.responses = responses
        self.commands: list[str] = []

    def run(
        self,
        command: list[str],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
        check: bool = True,
        capture_output: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        del input_text, env, timeout, capture_output
        command_text = " ".join(command)
        self.commands.append(command_text)
        if not self.responses:
            raise AssertionError(f"unexpected command: {command_text}")
        expected, returncode, stdout = self.responses.pop(0)
        if expected not in command_text:
            raise AssertionError(f"expected {expected!r}, got {command_text!r}")
        result = subprocess.CompletedProcess(command, returncode, stdout, "error")
        if check and returncode != 0:
            raise vllm.CommandError(command, result)
        return result


@dataclasses.dataclass(frozen=True)
class FastConfig(vllm.Config):
    window_seconds: int = 0


def run_case(responses: list[tuple[str, int, str]]) -> tuple[int, list[str]]:
    runner = FakeRunner(responses)
    result = vllm.Orchestrator(FastConfig(), runner).run()
    return result, runner.commands


def guest_output(output: str = "", exitcode: int = 0) -> str:
    """Return the QGA envelope even when the host SSH command succeeds."""
    return json.dumps({"exited": 1, "exitcode": exitcode, "out-data": output})


class TestVllmWeeklyWindow:
    def test_hosts_already_on_starts_and_cleans_up_vllm(self) -> None:
        result, commands = run_case(
            [
                ("nc -z -w 2 proxmox-cortex.home.arpa 22", 0, ""),
                (
                    "ssh -o BatchMode=yes proxmox-cortex.home.arpa qm status 220",
                    0,
                    "status: running\n",
                ),
                ("nc -z -w 1 cognition.home.arpa 22", 0, ""),
                ("ps --services --filter status=running", 0, guest_output()),
                (
                    "ssh -o BatchMode=yes proxmox-cortex.home.arpa qm guest exec 220",
                    0,
                    guest_output(),
                ),
                (
                    "ssh -o BatchMode=yes proxmox-cortex.home.arpa qm guest exec 220",
                    0,
                    guest_output(),
                ),
            ]
        )

        assert result == 0
        assert any(
            "docker compose --profile extraction up --wait --wait-timeout 600 --no-recreate"
            in cmd
            for cmd in commands
        )
        assert any(
            "docker compose --profile extraction down" in cmd for cmd in commands
        )
        assert all("qm shutdown 220" not in cmd for cmd in commands)
        assert all("shutdown -h now" not in cmd for cmd in commands)

    def test_already_running_vllm_is_left_alone(self) -> None:
        result, commands = run_case(
            [
                ("nc -z -w 2 proxmox-cortex.home.arpa 22", 0, ""),
                (
                    "ssh -o BatchMode=yes proxmox-cortex.home.arpa qm status 220",
                    0,
                    "status: running\n",
                ),
                ("nc -z -w 1 cognition.home.arpa 22", 0, ""),
                (
                    "ps --services --filter status=running",
                    0,
                    guest_output("bge\npaddleocr-gateway\n"),
                ),
                ("up --wait --wait-timeout 600 --no-recreate", 0, guest_output()),
            ]
        )

        assert result == 0
        assert all(
            "docker compose --profile extraction down" not in cmd for cmd in commands
        )
        assert any(
            "up --wait --wait-timeout 600 --no-recreate" in cmd for cmd in commands
        )
        assert all("qm shutdown 220" not in cmd for cmd in commands)

    def test_full_sequence_cleans_up_started_layers(self) -> None:
        result, commands = run_case(
            [
                ("nc -z -w 2 proxmox-cortex.home.arpa 22", 1, ""),
                ("vault kv get -field=proxmox-cortex kv/wolf", 0, "secret\n"),
                ("wakeonlan", 0, ""),
                ("nc -z -w 1 dropbear.proxmox-cortex.home.arpa 2222", 0, ""),
                ("expect -", 0, ""),
                ("nc -z -w 1 proxmox-cortex.home.arpa 22", 0, ""),
                (
                    "ssh -o BatchMode=yes proxmox-cortex.home.arpa qm status 220",
                    0,
                    "status: stopped\n",
                ),
                ("ssh -o BatchMode=yes proxmox-cortex.home.arpa qm start 220", 0, ""),
                ("nc -z -w 1 cognition.home.arpa 22", 0, ""),
                ("ps --services --filter status=running", 0, guest_output()),
                (
                    "ssh -o BatchMode=yes proxmox-cortex.home.arpa qm guest exec 220",
                    0,
                    guest_output(),
                ),
                ("docker compose --profile extraction down", 0, guest_output()),
                (
                    "ssh -o BatchMode=yes proxmox-cortex.home.arpa qm shutdown 220",
                    0,
                    "",
                ),
                (
                    "ssh -o BatchMode=yes proxmox-cortex.home.arpa shutdown -h now",
                    0,
                    "",
                ),
            ]
        )

        assert result == 0
        assert "qm start 220" in " ".join(commands)
        assert any(
            "docker compose --profile extraction up --wait --wait-timeout 600 --no-recreate"
            in cmd
            for cmd in commands
        )
        assert any(
            "docker compose --profile extraction down" in cmd for cmd in commands
        )
        assert commands[-2].endswith("qm shutdown 220")
        assert commands[-1].endswith("shutdown -h now 'vLLM weekly window complete'")

    def test_compose_start_failure_still_cleans_up(self) -> None:
        result, commands = run_case(
            [
                ("nc -z -w 2 proxmox-cortex.home.arpa 22", 1, ""),
                ("vault kv get -field=proxmox-cortex kv/wolf", 0, "secret\n"),
                ("wakeonlan", 0, ""),
                ("nc -z -w 1 dropbear.proxmox-cortex.home.arpa 2222", 0, ""),
                ("expect -", 0, ""),
                ("nc -z -w 1 proxmox-cortex.home.arpa 22", 0, ""),
                (
                    "ssh -o BatchMode=yes proxmox-cortex.home.arpa qm status 220",
                    0,
                    "status: stopped\n",
                ),
                ("ssh -o BatchMode=yes proxmox-cortex.home.arpa qm start 220", 0, ""),
                ("nc -z -w 1 cognition.home.arpa 22", 0, ""),
                ("ps --services --filter status=running", 0, guest_output()),
                (
                    "up --wait --wait-timeout 600 --no-recreate",
                    0,
                    guest_output(exitcode=1),
                ),
                (
                    "ssh -o BatchMode=yes proxmox-cortex.home.arpa qm guest exec 220",
                    0,
                    guest_output(),
                ),
                (
                    "ssh -o BatchMode=yes proxmox-cortex.home.arpa qm shutdown 220",
                    0,
                    "",
                ),
                (
                    "ssh -o BatchMode=yes proxmox-cortex.home.arpa shutdown -h now",
                    0,
                    "",
                ),
            ]
        )

        assert result == 1
        assert any(
            "docker compose --profile extraction down" in cmd for cmd in commands
        )
        assert commands[-2].endswith("qm shutdown 220")
        assert commands[-1].endswith("'vLLM weekly window complete'")

    def test_lock_contention_exits_75(self, tmp_path: Path) -> None:
        lock_path = str(tmp_path / "lock")
        lock = shared.acquire_lock(lock_path, "busy")
        with lock:
            result = vllm.main(["--lock-file", lock_path, "--dry-run"])

        assert result == 75

    def test_unhealthy_existing_stack_is_not_torn_down(self) -> None:
        """A failed Compose health check must preserve another operator's services."""
        result, commands = run_case(
            [
                ("nc -z -w 2 proxmox-cortex.home.arpa 22", 0, ""),
                ("qm status 220", 0, "status: running\n"),
                ("nc -z -w 1 cognition.home.arpa 22", 0, ""),
                ("ps --services --filter status=running", 0, guest_output("bge\n")),
                (
                    "up --wait --wait-timeout 600 --no-recreate",
                    0,
                    guest_output(exitcode=1),
                ),
            ]
        )
        assert result == 1
        assert all(" down" not in command for command in commands)
        assert all("qm shutdown" not in command for command in commands)

    def test_guest_command_must_finish_before_success(self) -> None:
        """A QGA PID alone is not a successful model startup."""
        runner = FakeRunner([("qm guest exec 220 --timeout 0", 0, '{"pid":123}')])
        controller = vllm.Orchestrator(FastConfig(), runner)
        with pytest.raises(RuntimeError, match="did not complete"):
            controller.guest_exec("docker compose up --wait", timeout=630)


def test_vllm_wrapper_passes_target_overrides(tmp_path):
    """Explicit target options reach the service instead of falling back to defaults."""
    from puppet_catalog import DEPENDENCIES, compile_catalog, resources

    if not (DEPENDENCIES / "systemd/manifests/unit_file.pp").is_file():
        pytest.skip("Set PUPPET_TEST_MODULEPATH to the installed Forge dependencies")

    catalog = compile_catalog(
        tmp_path,
        "service { 'alloy': } class { 'profile::vllm_weekly_window': vm_host => 'fixture-workload.home.arpa', vm_id => 999 }",
        hostname="proxmox",
        node_type="proxmox",
    )
    service = resources(catalog)[
        "File[/etc/systemd/system/vllm-weekly-window.service]"
    ]["content"]
    assert "--vm-host fixture-workload.home.arpa --vm-id 999" in service
