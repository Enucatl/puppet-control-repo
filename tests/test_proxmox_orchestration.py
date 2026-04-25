from __future__ import annotations

import dataclasses
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = (
    Path(__file__).parents[1]
    / "modules"
    / "profile"
    / "files"
    / "proxmox_orchestration.py"
)
SPEC = importlib.util.spec_from_file_location("proxmox_orchestration", MODULE_PATH)
assert SPEC is not None
shared = importlib.util.module_from_spec(SPEC)
sys.modules["proxmox_orchestration"] = shared
assert SPEC.loader is not None
SPEC.loader.exec_module(shared)


@dataclasses.dataclass(frozen=True)
class Config:
    dropbear_host: str = "dropbear.proxmox-cortex.home.arpa"
    proxmox_host: str = "proxmox-cortex.home.arpa"
    mac: str = "30:56:0f:5e:a9:de"
    broadcast: str = "10.0.0.255"
    vault_path: str = "kv/puppet"
    vault_field: str = "proxmox-cortex"
    vault_addr: str = "https://hcv.home.arpa:8200"
    vault_cacert: str = "/etc/ssl/certs/ca-certificates.crt"
    vault_cert_role: str = "puppet"
    certname: str = "proxmox.home.arpa"
    command_timeout: int = 60
    dry_run: bool = False


class FakeRunner:
    def __init__(self, responses: list[tuple[str, int, str]]) -> None:
        self.responses = responses
        self.commands: list[str] = []
        self.envs: list[dict[str, str] | None] = []

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
        del input_text, timeout, capture_output
        command_text = " ".join(command)
        self.commands.append(command_text)
        self.envs.append(env)
        if not self.responses:
            raise AssertionError(f"unexpected command: {command_text}")
        expected, returncode, stdout = self.responses.pop(0)
        if expected not in command_text:
            raise AssertionError(f"expected {expected!r}, got {command_text!r}")
        result = subprocess.CompletedProcess(command, returncode, stdout, "error")
        if check and returncode != 0:
            raise shared.CommandError(command, result)
        return result


class ProxmoxOrchestrationTest(unittest.TestCase):
    def test_vault_secret_uses_cert_login_fallback(self) -> None:
        runner = FakeRunner(
            [
                ("vault kv get -field=proxmox-cortex kv/puppet", 1, ""),
                (
                    "vault login -client-cert=/etc/puppetlabs/puppet/ssl/certs/proxmox.home.arpa.pem",
                    0,
                    '{"auth":{"client_token":"token"}}',
                ),
                ("vault kv get -field=proxmox-cortex kv/puppet", 0, "secret\n"),
            ]
        )

        secret = shared.read_vault_secret(Config(), runner)

        self.assertEqual(secret, "secret")
        self.assertEqual(runner.envs[-1]["VAULT_TOKEN"], "token")

    def test_lock_contention_raises_runtime_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = str(Path(temp_dir) / "lock")
            lock = shared.acquire_lock(lock_path, "busy")
            with lock:
                with self.assertRaisesRegex(RuntimeError, "busy"):
                    shared.acquire_lock(lock_path, "busy")

    def test_wait_for_port_polls_until_open(self) -> None:
        runner = FakeRunner(
            [
                ("nc -z -w 1 host 22", 1, ""),
                ("nc -z -w 1 host 22", 0, ""),
            ]
        )

        shared.wait_for_port(runner, "host", 22, "SSH", 5, 0)

        self.assertEqual(len(runner.commands), 2)

    def test_wake_and_unlock_command_order(self) -> None:
        runner = FakeRunner(
            [
                ("wakeonlan -i 10.0.0.255 30:56:0f:5e:a9:de", 0, ""),
                ("nc -z -w 1 dropbear.proxmox-cortex.home.arpa 2222", 0, ""),
                ("expect -", 0, ""),
                ("nc -z -w 1 proxmox-cortex.home.arpa 22", 0, ""),
            ]
        )

        shared.wake_and_unlock(Config(), runner, "secret")

        self.assertEqual(
            runner.commands,
            [
                "wakeonlan -i 10.0.0.255 30:56:0f:5e:a9:de",
                "nc -z -w 1 dropbear.proxmox-cortex.home.arpa 2222",
                "expect -",
                "nc -z -w 1 proxmox-cortex.home.arpa 22",
            ],
        )


if __name__ == "__main__":
    unittest.main()
