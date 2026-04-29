from __future__ import annotations

import dataclasses
import importlib.util
import subprocess
import sys
from pathlib import Path


FILES_DIR = Path(__file__).parents[1] / "modules" / "profile" / "files"
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


class TestVllmWeeklyWindow:
    def test_already_on_is_noop(self) -> None:
        result, commands = run_case([("nc -z -w 2 proxmox-cortex.home.arpa 22", 0, "")])

        assert result == 0
        assert commands == ["nc -z -w 2 proxmox-cortex.home.arpa 22"]

    def test_full_sequence_cleans_up_started_layers(self) -> None:
        result, commands = run_case(
            [
                ("nc -z -w 2 proxmox-cortex.home.arpa 22", 1, ""),
                ("vault kv get", 0, "secret\n"),
                ("wakeonlan", 0, ""),
                ("nc -z -w 1 dropbear.proxmox-cortex.home.arpa 2222", 0, ""),
                ("expect -", 0, ""),
                ("nc -z -w 1 proxmox-cortex.home.arpa 22", 0, ""),
                (
                    "ssh -o BatchMode=yes proxmox-cortex.home.arpa qm status 200",
                    0,
                    "status: stopped\n",
                ),
                ("ssh -o BatchMode=yes proxmox-cortex.home.arpa qm start 200", 0, ""),
                ("nc -z -w 1 complex.home.arpa 22", 0, ""),
                (
                    "ssh -o BatchMode=yes proxmox-cortex.home.arpa qm guest exec 200",
                    0,
                    "",
                ),
                (
                    "ssh -o BatchMode=yes proxmox-cortex.home.arpa qm guest exec 200",
                    0,
                    "",
                ),
                (
                    "ssh -o BatchMode=yes proxmox-cortex.home.arpa qm shutdown 200",
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
        assert "qm start 200" in " ".join(commands)
        assert "docker compose --profile extraction up -d" in commands[9]
        assert "docker compose --profile extraction down" in commands[10]
        assert commands[-2].endswith("qm shutdown 200")
        assert commands[-1].endswith("shutdown -h now 'vLLM weekly window complete'")

    def test_compose_start_failure_still_cleans_up(self) -> None:
        result, commands = run_case(
            [
                ("nc -z -w 2 proxmox-cortex.home.arpa 22", 1, ""),
                ("vault kv get", 0, "secret\n"),
                ("wakeonlan", 0, ""),
                ("nc -z -w 1 dropbear.proxmox-cortex.home.arpa 2222", 0, ""),
                ("expect -", 0, ""),
                ("nc -z -w 1 proxmox-cortex.home.arpa 22", 0, ""),
                (
                    "ssh -o BatchMode=yes proxmox-cortex.home.arpa qm status 200",
                    0,
                    "status: stopped\n",
                ),
                ("ssh -o BatchMode=yes proxmox-cortex.home.arpa qm start 200", 0, ""),
                ("nc -z -w 1 complex.home.arpa 22", 0, ""),
                (
                    "ssh -o BatchMode=yes proxmox-cortex.home.arpa qm guest exec 200",
                    1,
                    "",
                ),
                (
                    "ssh -o BatchMode=yes proxmox-cortex.home.arpa qm guest exec 200",
                    0,
                    "",
                ),
                (
                    "ssh -o BatchMode=yes proxmox-cortex.home.arpa qm shutdown 200",
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
        assert "docker compose --profile extraction down" in commands[10]
        assert commands[-2].endswith("qm shutdown 200")
        assert commands[-1].endswith("'vLLM weekly window complete'")

    def test_lock_contention_exits_75(self, tmp_path: Path) -> None:
        lock_path = str(tmp_path / "lock")
        lock = shared.acquire_lock(lock_path, "busy")
        with lock:
            result = vllm.main(["--lock-file", lock_path, "--dry-run"])

        assert result == 75
