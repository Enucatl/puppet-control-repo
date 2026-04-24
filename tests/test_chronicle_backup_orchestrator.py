from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


MODULE_PATH = (
    Path(__file__).parents[1]
    / "modules"
    / "profile"
    / "files"
    / "chronicle_backup_orchestrator.py"
)
SPEC = importlib.util.spec_from_file_location(
    "chronicle_backup_orchestrator", MODULE_PATH
)
assert SPEC is not None
orchestrator = importlib.util.module_from_spec(SPEC)
sys.modules["chronicle_backup_orchestrator"] = orchestrator
assert SPEC.loader is not None
SPEC.loader.exec_module(orchestrator)


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
    ) -> subprocess.CompletedProcess[str]:
        del input_text, env, timeout
        command_text = " ".join(command)
        self.commands.append(command_text)
        if not self.responses:
            raise AssertionError(f"unexpected command: {command_text}")
        expected, returncode, stdout = self.responses.pop(0)
        if expected not in command_text:
            raise AssertionError(f"expected {expected!r}, got {command_text!r}")
        result = subprocess.CompletedProcess(command, returncode, stdout, "error")
        if check and returncode != 0:
            raise orchestrator.CommandError(command, result)
        return result


class NoSleepOrchestrator(orchestrator.Orchestrator):
    def wait_for_port(
        self, host: str, port: int, label: str, timeout: int, sleep_interval: int
    ) -> None:
        del label, timeout, sleep_interval
        if not self.port_open(host, port):
            raise RuntimeError(f"{host}:{port} unavailable")


def run_case(responses: list[tuple[str, int, str]]) -> tuple[int, list[str]]:
    runner = FakeRunner(responses)
    result = NoSleepOrchestrator(orchestrator.Config(), runner).run()
    return result, runner.commands


class ChronicleBackupOrchestratorTest(unittest.TestCase):
    def test_initially_off_runs_full_sequence_and_shutdown(self) -> None:
        result, commands = run_case(
            [
                ("vault kv get", 0, "secret\n"),
                ("nc -z -w 2 proxmox-cortex.home.arpa 22", 1, ""),
                ("wakeonlan", 0, ""),
                ("nc -z -w 1 dropbear.proxmox-cortex.home.arpa 2222", 0, ""),
                ("expect -", 0, ""),
                ("nc -z -w 1 proxmox-cortex.home.arpa 22", 0, ""),
                (
                    "ssh -o BatchMode=yes proxmox-cortex.home.arpa pct status 110",
                    0,
                    "status: stopped\n",
                ),
                ("ssh -o BatchMode=yes proxmox-cortex.home.arpa pct start 110", 0, ""),
                ("nc -z -w 1 chronicle.home.arpa 8007", 0, ""),
                (
                    "curl --fail --silent --show-error --insecure "
                    "https://chronicle.home.arpa:8007/api2/json/version",
                    0,
                    "{}",
                ),
                ("pvesm set chronicle --disable 0", 0, ""),
                (
                    "pvesh create /cluster/backup/pbs-chronicle-weekly/run",
                    0,
                    '"UPID:proxmox:1:2:3:vzdump::root@pam:"',
                ),
                (
                    "pvesh get /nodes/proxmox/tasks/UPID:proxmox",
                    0,
                    '{"status":"stopped","exitstatus":"OK"}',
                ),
                ("pvesm set chronicle --disable 1", 0, ""),
                ("ssh -o BatchMode=yes proxmox-cortex.home.arpa shutdown", 0, ""),
            ]
        )

        self.assertEqual(result, 0)
        self.assertIn("wakeonlan -i 10.0.0.255 30:56:0f:5e:a9:de", commands)
        self.assertTrue(
            commands[-1].endswith("shutdown -h now 'Chronicle backup complete'")
        )

    def test_initially_on_skips_wake_unlock_and_shutdown(self) -> None:
        result, commands = run_case(
            [
                ("vault kv get", 0, "secret\n"),
                ("nc -z -w 2 proxmox-cortex.home.arpa 22", 0, ""),
                (
                    "ssh -o BatchMode=yes proxmox-cortex.home.arpa pct status 110",
                    0,
                    "status: stopped\n",
                ),
                ("ssh -o BatchMode=yes proxmox-cortex.home.arpa pct start 110", 0, ""),
                ("nc -z -w 1 chronicle.home.arpa 8007", 0, ""),
                (
                    "curl --fail --silent --show-error --insecure "
                    "https://chronicle.home.arpa:8007/api2/json/version",
                    0,
                    "{}",
                ),
                ("pvesm set chronicle --disable 0", 0, ""),
                (
                    "pvesh create /cluster/backup/pbs-chronicle-weekly/run",
                    0,
                    '{"upid":"UPID:proxmox:1:2:3:vzdump::root@pam:"}',
                ),
                (
                    "pvesh get /nodes/proxmox/tasks/UPID:proxmox",
                    0,
                    '{"status":"stopped","exitstatus":"OK"}',
                ),
                ("pvesm set chronicle --disable 1", 0, ""),
            ]
        )

        self.assertEqual(result, 0)
        self.assertFalse(any("wakeonlan" in command for command in commands))
        self.assertFalse(any("shutdown -h now" in command for command in commands))

    def test_ct_already_running_does_not_start_it(self) -> None:
        result, commands = run_case(
            [
                ("vault kv get", 0, "secret\n"),
                ("nc -z -w 2 proxmox-cortex.home.arpa 22", 0, ""),
                (
                    "ssh -o BatchMode=yes proxmox-cortex.home.arpa pct status 110",
                    0,
                    "status: running\n",
                ),
                ("nc -z -w 1 chronicle.home.arpa 8007", 0, ""),
                (
                    "curl --fail --silent --show-error --insecure "
                    "https://chronicle.home.arpa:8007/api2/json/version",
                    0,
                    "{}",
                ),
                ("pvesm set chronicle --disable 0", 0, ""),
                (
                    "pvesh create /cluster/backup/pbs-chronicle-weekly/run",
                    0,
                    "UPID:proxmox:1:2:3:vzdump::root@pam:",
                ),
                (
                    "pvesh get /nodes/proxmox/tasks/UPID:proxmox",
                    0,
                    '{"status":"stopped","exitstatus":"OK"}',
                ),
                ("pvesm set chronicle --disable 1", 0, ""),
            ]
        )

        self.assertEqual(result, 0)
        self.assertFalse(any("pct start 110" in command for command in commands))

    def test_backup_task_failure_cleans_up_and_exits_nonzero(self) -> None:
        result, commands = run_case(
            [
                ("vault kv get", 0, "secret\n"),
                ("nc -z -w 2 proxmox-cortex.home.arpa 22", 0, ""),
                (
                    "ssh -o BatchMode=yes proxmox-cortex.home.arpa pct status 110",
                    0,
                    "status: running\n",
                ),
                ("nc -z -w 1 chronicle.home.arpa 8007", 0, ""),
                (
                    "curl --fail --silent --show-error --insecure "
                    "https://chronicle.home.arpa:8007/api2/json/version",
                    0,
                    "{}",
                ),
                ("pvesm set chronicle --disable 0", 0, ""),
                (
                    "pvesh create /cluster/backup/pbs-chronicle-weekly/run",
                    0,
                    "UPID:proxmox:1:2:3:vzdump::root@pam:",
                ),
                (
                    "pvesh get /nodes/proxmox/tasks/UPID:proxmox",
                    0,
                    '{"status":"stopped","exitstatus":"error"}',
                ),
                ("pvesm set chronicle --disable 1", 0, ""),
            ]
        )

        self.assertEqual(result, 1)
        self.assertTrue(commands[-1].startswith("pvesm set chronicle --disable 1"))

    def test_storage_enable_failure_attempts_cleanup(self) -> None:
        result, commands = run_case(
            [
                ("vault kv get", 0, "secret\n"),
                ("nc -z -w 2 proxmox-cortex.home.arpa 22", 0, ""),
                (
                    "ssh -o BatchMode=yes proxmox-cortex.home.arpa pct status 110",
                    0,
                    "status: running\n",
                ),
                ("nc -z -w 1 chronicle.home.arpa 8007", 0, ""),
                (
                    "curl --fail --silent --show-error --insecure "
                    "https://chronicle.home.arpa:8007/api2/json/version",
                    0,
                    "{}",
                ),
                ("pvesm set chronicle --disable 0", 1, ""),
                ("pvesm set chronicle --disable 1", 0, ""),
            ]
        )

        self.assertEqual(result, 1)
        self.assertTrue(commands[-1].startswith("pvesm set chronicle --disable 1"))

    def test_vault_failure_exits_before_wake(self) -> None:
        result, commands = run_case(
            [
                ("vault kv get", 1, ""),
                ("vault login -method=cert", 1, ""),
            ]
        )

        self.assertEqual(result, 1)
        self.assertFalse(any("wakeonlan" in command for command in commands))


class FakeCommandIntegrationTest(unittest.TestCase):
    def test_dry_run_exercises_sequence_without_real_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "--lock-file",
                    str(Path(temp_dir) / "lock"),
                    "--dry-run",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("wakeonlan -i 10.0.0.255 30:56:0f:5e:a9:de", result.stdout)
        self.assertIn("pvesm set chronicle --disable 1", result.stdout)

    def test_command_order_through_temporary_path_wrappers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            log_file = temp_path / "commands.log"
            state_file = temp_path / "nc-state"
            state_file.write_text("0", encoding="utf-8")

            self.write_wrapper(
                temp_path / "vault",
                """
                if [ "$1 $2 $3" = "kv get -field=proxmox-cortex" ]; then
                  echo secret
                  exit 0
                fi
                exit 1
                """,
                log_file,
            )
            self.write_wrapper(temp_path / "wakeonlan", "exit 0", log_file)
            self.write_wrapper(temp_path / "expect", "cat >/dev/null\nexit 0", log_file)
            self.write_wrapper(temp_path / "curl", "exit 0", log_file)
            self.write_wrapper(
                temp_path / "nc",
                f"""
                count="$(cat {state_file})"
                echo $((count + 1)) > {state_file}
                if [ "$5" = "22" ] && [ "$count" = "0" ]; then
                  exit 1
                fi
                exit 0
                """,
                log_file,
            )
            self.write_wrapper(
                temp_path / "ssh",
                """
                case "$*" in
                  *"pct status 110"*) echo "status: stopped"; exit 0 ;;
                  *"pct start 110"*) exit 0 ;;
                  *"shutdown -h now"*) exit 0 ;;
                esac
                exit 1
                """,
                log_file,
            )
            self.write_wrapper(
                temp_path / "pvesm",
                """
                [ "$*" = "set chronicle --disable 0" ] && exit 0
                [ "$*" = "set chronicle --disable 1" ] && exit 0
                exit 1
                """,
                log_file,
            )
            self.write_wrapper(
                temp_path / "pvesh",
                """
                case "$*" in
                  create*) echo '"UPID:proxmox:1:2:3:vzdump::root@pam:"'; exit 0 ;;
                  get*) echo '{"status":"stopped","exitstatus":"OK"}'; exit 0 ;;
                esac
                exit 1
                """,
                log_file,
            )

            env = os.environ.copy()
            env["PATH"] = f"{temp_path}:{env['PATH']}"
            result = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "--lock-file",
                    str(temp_path / "lock"),
                ],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            commands = log_file.read_text(encoding="utf-8").splitlines()
            self.assertEqual(
                commands,
                [
                    "vault kv get -field=proxmox-cortex kv/puppet",
                    "nc -z -w 2 proxmox-cortex.home.arpa 22",
                    "wakeonlan -i 10.0.0.255 30:56:0f:5e:a9:de",
                    "nc -z -w 1 dropbear.proxmox-cortex.home.arpa 2222",
                    "expect -",
                    "nc -z -w 1 proxmox-cortex.home.arpa 22",
                    "ssh -o BatchMode=yes proxmox-cortex.home.arpa pct status 110",
                    "ssh -o BatchMode=yes proxmox-cortex.home.arpa pct start 110",
                    "nc -z -w 1 chronicle.home.arpa 8007",
                    "curl --fail --silent --show-error --insecure https://chronicle.home.arpa:8007/api2/json/version",
                    "pvesm set chronicle --disable 0",
                    "pvesh create /cluster/backup/pbs-chronicle-weekly/run --output-format json",
                    "pvesh get /nodes/proxmox/tasks/UPID:proxmox:1:2:3:vzdump::root@pam:/status --output-format json",
                    "pvesm set chronicle --disable 1",
                    "ssh -o BatchMode=yes proxmox-cortex.home.arpa shutdown -h now 'Chronicle backup complete'",
                ],
            )

    def write_wrapper(self, path: Path, body: str, log_file: Path) -> None:
        path.write_text(
            "#!/bin/sh\n"
            f'echo "$(basename "$0") $*" >> {log_file}\n'
            + textwrap.dedent(body).strip()
            + "\n",
            encoding="utf-8",
        )
        path.chmod(0o755)


if __name__ == "__main__":
    unittest.main()
