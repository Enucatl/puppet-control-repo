#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import shlex
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, "/usr/local/lib")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import proxmox_orchestration

CommandError = proxmox_orchestration.CommandError
Runner = proxmox_orchestration.Runner


@dataclasses.dataclass(frozen=True)
class Config:
    dropbear_host: str = "dropbear.proxmox-cortex.home.arpa"
    proxmox_host: str = "proxmox-cortex.home.arpa"
    vm_host: str = "complex.home.arpa"
    guest_user: str = "user"
    vm_id: int = 200
    mac: str = "30:56:0f:5e:a9:de"
    broadcast: str = "10.0.0.255"
    compose_dir: str = "/opt/docker/vllm"
    compose_env_files: str = "../.env,./.env"
    compose_profile: str = "extraction"
    window_seconds: int = 3600
    vault_path: str = "kv/puppet"
    vault_field: str = "proxmox-cortex"
    vault_addr: str = "https://hcv.home.arpa:8200"
    vault_cacert: str = "/etc/ssl/certs/ca-certificates.crt"
    vault_cert_role: str = "proxmox-puppet"
    certname: str = "proxmox.home.arpa"
    lock_file: str = "/run/vllm-weekly-window.lock"
    command_timeout: int = 60
    dry_run: bool = False


class Orchestrator:
    def __init__(self, config: Config, runner: Runner) -> None:
        self.config = config
        self.runner = runner
        self.woke_host = False
        self.reliable_proxmox_ssh = False
        self.started_vm = False
        self.vm_shutdown_needed = False
        self.compose_up_attempted = False

    def run(self) -> int:
        try:
            if proxmox_orchestration.port_open(
                self.runner, self.config.proxmox_host, 22, timeout=2
            ):
                print("proxmox-cortex SSH is already reachable; no vLLM window needed")
                return 0

            password = proxmox_orchestration.read_vault_secret(self.config, self.runner)
            proxmox_orchestration.wake_and_unlock(self.config, self.runner, password)
            self.woke_host = True
            self.reliable_proxmox_ssh = True

            self.ensure_vm_running()
            proxmox_orchestration.wait_for_port(
                self.runner, self.config.vm_host, 22, "complex SSH", 300, 5
            )
            self.compose_up()
            time.sleep(self.config.window_seconds)
            return 0
        except Exception as error:
            print(f"vLLM weekly window failed: {error}", file=sys.stderr)
            return 1
        finally:
            self.cleanup()

    def ensure_vm_running(self) -> None:
        status = self.proxmox_ssh([f"qm status {self.config.vm_id}"], check=False)
        if "status: running" in status.stdout:
            print(f"VM {self.config.vm_id} is already running")
            self.vm_shutdown_needed = True
            return
        self.proxmox_ssh([f"qm start {self.config.vm_id}"], timeout=120)
        self.started_vm = True
        self.vm_shutdown_needed = True

    def compose_up(self) -> None:
        self.compose_up_attempted = True
        self.guest_exec(self.compose_command("up -d"), timeout=300)

    def cleanup(self) -> None:
        if self.compose_up_attempted:
            try:
                self.guest_exec(self.compose_command("down"), timeout=300, check=False)
            except Exception as error:
                print(f"compose cleanup failed: {error}", file=sys.stderr)

        if self.vm_shutdown_needed and self.reliable_proxmox_ssh:
            try:
                self.proxmox_ssh([f"qm shutdown {self.config.vm_id}"], check=False)
            except Exception as error:
                print(f"VM shutdown cleanup failed: {error}", file=sys.stderr)

        if self.woke_host and self.reliable_proxmox_ssh:
            try:
                self.proxmox_ssh(
                    ["shutdown -h now 'vLLM weekly window complete'"], check=False
                )
            except Exception as error:
                print(f"host shutdown cleanup failed: {error}", file=sys.stderr)

    def compose_command(self, action: str) -> str:
        return (
            f"cd {self.config.compose_dir} && "
            f"COMPOSE_ENV_FILES={self.config.compose_env_files} "
            f"docker compose --profile {self.config.compose_profile} {action}"
        )

    def proxmox_ssh(
        self,
        remote_commands: Sequence[str],
        *,
        timeout: int | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return proxmox_orchestration.proxmox_ssh(
            self.config,
            self.runner,
            remote_commands,
            timeout=timeout,
            check=check,
        )

    def guest_exec(
        self, command: str, *, timeout: int | None = None, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        guest_command = (
            f"qm guest exec {self.config.vm_id} -- /bin/su - "
            f"{shlex.quote(self.config.guest_user)} -c {shlex.quote(command)}"
        )
        return self.proxmox_ssh(
            [guest_command],
            timeout=timeout,
            check=check,
        )


def parse_args(argv: Sequence[str]) -> Config:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dropbear-host", default=Config.dropbear_host)
    parser.add_argument("--proxmox-host", default=Config.proxmox_host)
    parser.add_argument("--vm-host", default=Config.vm_host)
    parser.add_argument("--guest-user", default=Config.guest_user)
    parser.add_argument("--vm-id", type=int, default=Config.vm_id)
    parser.add_argument("--mac", default=Config.mac)
    parser.add_argument("--broadcast", default=Config.broadcast)
    parser.add_argument("--compose-dir", default=Config.compose_dir)
    parser.add_argument("--compose-env-files", default=Config.compose_env_files)
    parser.add_argument("--compose-profile", default=Config.compose_profile)
    parser.add_argument("--window-seconds", type=int, default=Config.window_seconds)
    parser.add_argument("--vault-path", default=Config.vault_path)
    parser.add_argument("--vault-field", default=Config.vault_field)
    parser.add_argument("--vault-cert-role", default=Config.vault_cert_role)
    parser.add_argument("--certname", default=Config.certname)
    parser.add_argument("--lock-file", default=Config.lock_file)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    return Config(**vars(args))


def main(argv: Sequence[str] | None = None) -> int:
    config = parse_args(argv or sys.argv[1:])
    try:
        lock = proxmox_orchestration.acquire_lock(
            config.lock_file, "another vLLM weekly window is already active"
        )
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 75
    with lock:
        return Orchestrator(config, Runner(config)).run()


if __name__ == "__main__":
    raise SystemExit(main())
