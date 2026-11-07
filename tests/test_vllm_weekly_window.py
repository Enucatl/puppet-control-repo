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
        self.timeouts: list[int | None] = []

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
        del input_text, env, capture_output
        self.timeouts.append(timeout)
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


def running_host_responses(existing: str = "") -> list[tuple[str, int, str]]:
    """Reach a running VM and snapshot ownership before model startup."""
    return [
        ("nc -z -w 2 proxmox-cortex.home.arpa 22", 0, ""),
        ("qm status 230", 0, "status: running\n"),
        ("nc -z -w 1 cerebrum.home.arpa 22", 0, ""),
        ("config --services", 0, guest_output("bge\npaddleocr-gateway\n")),
        ("ps --services --filter status=running", 0, guest_output(existing)),
        ("up --wait --wait-timeout 600 --no-recreate", 0, guest_output()),
    ]


def worker_response(exitcode: int = 0) -> tuple[str, int, str]:
    """Finish one standalone worker pass after model health succeeds."""
    return (
        "docker compose run --rm --no-deps workstation --once",
        0,
        guest_output(exitcode=exitcode),
    )


class TestVllmWeeklyWindow:
    def test_hosts_already_on_runs_worker_then_stops_owned_models(self) -> None:
        """Even a zero window completes one pass before model cleanup."""
        runner = FakeRunner(
            running_host_responses()
            + [
                worker_response(),
                ("stop bge paddleocr-gateway", 0, guest_output()),
            ]
        )
        assert vllm.Orchestrator(FastConfig(), runner).run() == 0
        assert not runner.responses
        worker_index = next(
            i for i, cmd in enumerate(runner.commands) if "workstation --once" in cmd
        )
        assert "/opt/docker/vllm/paperless-workstation" in runner.commands[worker_index]
        assert runner.timeouts[worker_index] == 0
        assert all(
            "qm shutdown" not in cmd and "shutdown -h now" not in cmd
            for cmd in runner.commands
        )

    def test_existing_models_still_run_worker_and_remain_running(self) -> None:
        """A preexisting healthy stack does not suppress weekly processing."""
        result, commands = run_case(
            running_host_responses("bge\npaddleocr-gateway\n") + [worker_response()]
        )
        assert result == 0
        assert any("workstation --once" in command for command in commands)
        assert all(
            " stop " not in command and " down" not in command for command in commands
        )

    def test_partial_existing_stack_preserves_its_services(self) -> None:
        """Cleanup targets only services absent from the running snapshot."""
        runner = FakeRunner(
            running_host_responses("bge\nqwen\n")
            + [
                worker_response(),
                ("stop paddleocr-gateway", 0, guest_output()),
            ]
        )
        assert vllm.Orchestrator(FastConfig(), runner).run() == 0
        assert not runner.responses
        assert "stop bge" not in " ".join(runner.commands)

    def test_full_sequence_cleans_up_started_layers(self) -> None:
        """The worker exits before owned models, VM and host are stopped."""
        responses = (
            [
                ("nc -z -w 2 proxmox-cortex.home.arpa 22", 1, ""),
                ("vault kv get -field=proxmox-cortex kv/wolf", 0, "secret\n"),
                ("wakeonlan", 0, ""),
                ("nc -z -w 1 dropbear.proxmox-cortex.home.arpa 2222", 0, ""),
                ("expect -", 0, ""),
                ("nc -z -w 1 proxmox-cortex.home.arpa 22", 0, ""),
                ("qm status 230", 0, "status: stopped\n"),
                ("qm start 230", 0, ""),
            ]
            + running_host_responses()[2:]
            + [
                worker_response(),
                ("stop bge paddleocr-gateway", 0, guest_output()),
                ("qm shutdown 230", 0, ""),
                ("shutdown -h now", 0, ""),
            ]
        )
        runner = FakeRunner(responses)
        assert vllm.Orchestrator(FastConfig(), runner).run() == 0
        assert not runner.responses
        assert runner.commands[-2].endswith("qm shutdown 230")
        assert runner.commands[-1].endswith(
            "shutdown -h now 'vLLM weekly window complete'"
        )

    def test_compose_start_failure_cleans_only_owned_models(self) -> None:
        """Unhealthy models never launch a worker and preserve existing services."""
        responses = running_host_responses("bge\n")
        responses[-1] = (
            "up --wait --wait-timeout 600 --no-recreate",
            0,
            guest_output(exitcode=1),
        )
        runner = FakeRunner(responses + [("stop paddleocr-gateway", 0, guest_output())])
        assert vllm.Orchestrator(FastConfig(), runner).run() == 1
        assert not runner.responses
        assert all("workstation --once" not in command for command in runner.commands)

    @pytest.mark.parametrize(
        "worker", [worker_response(1), ("workstation --once", 255, "")]
    )
    def test_uncertain_worker_completion_preserves_all_layers(self, worker) -> None:
        """A failed or disconnected pass must not trigger destructive cleanup."""
        runner = FakeRunner(running_host_responses() + [worker])
        controller = vllm.Orchestrator(FastConfig(), runner)
        controller.started_vm = True
        controller.woke_host = True
        assert controller.run() == 1
        assert not runner.responses
        assert all(
            " stop " not in cmd and "shutdown" not in cmd for cmd in runner.commands
        )

    def test_window_waits_for_serial_passes_and_dry_run_never_sleeps(
        self, monkeypatch
    ) -> None:
        """Stop admitting new passes at the deadline without interrupting one."""
        ticks = iter([0, 0, 0, 10, 40, 70])
        monkeypatch.setattr(vllm.time, "monotonic", lambda: next(ticks))
        sleeps = []
        monkeypatch.setattr(vllm.time, "sleep", sleeps.append)
        runner = FakeRunner(
            running_host_responses()
            + [
                worker_response(),
                worker_response(),
                ("stop bge paddleocr-gateway", 0, guest_output()),
            ]
        )
        assert (
            vllm.Orchestrator(
                dataclasses.replace(FastConfig(), window_seconds=60), runner
            ).run()
            == 0
        )
        assert not runner.responses
        assert sleeps == [30]
        ticks = iter([0] * 20)
        monkeypatch.setattr(vllm.time, "monotonic", lambda: next(ticks))
        controller = vllm.Orchestrator(
            dataclasses.replace(FastConfig(), window_seconds=3600, dry_run=True),
            shared.Runner(dataclasses.replace(FastConfig(), dry_run=True)),
        )
        assert controller.run() == 0
        assert sleeps == [30]

    def test_lock_contention_exits_75(self, tmp_path: Path) -> None:
        """Keep the single orchestrator lock across the entire weekly window."""
        lock_path = str(tmp_path / "lock")
        lock = shared.acquire_lock(lock_path, "busy")
        with lock:
            result = vllm.main(["--lock-file", lock_path, "--dry-run"])
        assert result == 75

    def test_guest_command_must_finish_before_success(self) -> None:
        """A QGA PID alone is not a successful model startup."""
        runner = FakeRunner([("qm guest exec 230 --timeout 0", 0, '{"pid":123}')])
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
