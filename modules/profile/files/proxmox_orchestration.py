from __future__ import annotations

import fcntl
import json
import os
import subprocess
import textwrap
import time
from collections.abc import Sequence
from typing import Protocol


class ProxmoxConfig(Protocol):
    dropbear_host: str
    proxmox_host: str
    mac: str
    broadcast: str
    vault_path: str
    vault_field: str
    vault_addr: str
    vault_cacert: str
    vault_cert_role: str
    certname: str
    command_timeout: int
    dry_run: bool


class CommandError(RuntimeError):
    def __init__(
        self, command: Sequence[str], result: subprocess.CompletedProcess[str]
    ):
        self.command = command
        self.result = result
        super().__init__(
            f"{' '.join(command)} failed with exit {result.returncode}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )


class Runner:
    def __init__(self, config: ProxmoxConfig) -> None:
        self.config = config
        self.dry_run_nc_checks = 0

    def run(
        self,
        command: Sequence[str],
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
        check: bool = True,
        capture_output: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        print(f"+ {' '.join(command)}", flush=True)
        if self.config.dry_run:
            return self.dry_run_result(command)
        run_kwargs: dict[str, object] = {
            "input": input_text,
            "text": True,
            "env": env,
            "timeout": (
                self.config.command_timeout
                if timeout is None
                else None
                if timeout == 0
                else timeout
            ),
            "check": False,
        }
        if capture_output:
            run_kwargs["capture_output"] = True
        result = subprocess.run(list(command), **run_kwargs)
        if check and result.returncode != 0:
            raise CommandError(command, result)
        return result

    def dry_run_result(
        self, command: Sequence[str]
    ) -> subprocess.CompletedProcess[str]:
        command_text = " ".join(command)
        if command[:3] == ["vault", "kv", "get"]:
            return subprocess.CompletedProcess(command, 0, "dry-run-secret\n", "")
        if command[:1] == ["nc"]:
            self.dry_run_nc_checks += 1
            returncode = 1 if self.dry_run_nc_checks == 1 else 0
            return subprocess.CompletedProcess(command, returncode, "", "")
        if "pct status" in command_text or "qm status" in command_text:
            return subprocess.CompletedProcess(command, 0, "status: stopped\n", "")
        if command[:2] == ["pvesh", "create"]:
            backup_job_id = getattr(self.config, "backup_job_id", "dry-run")
            return subprocess.CompletedProcess(
                command,
                0,
                f'"UPID:proxmox:dry:run:task:vzdump:{backup_job_id}:root@pam:"\n',
                "",
            )
        if command[:3] == [
            "pvesh",
            "get",
            f"/cluster/backup/{getattr(self.config, 'backup_job_id', 'dry-run')}",
        ]:
            return subprocess.CompletedProcess(
                command,
                0,
                '{"all":1,"mode":"snapshot","storage":"chronicle"}\n',
                "",
            )
        if command[:2] == ["pvesh", "get"]:
            return subprocess.CompletedProcess(
                command,
                0,
                '{"status":"stopped","exitstatus":"OK"}\n',
                "",
            )
        return subprocess.CompletedProcess(command, 0, "", "")


def puppet_cert(config: ProxmoxConfig) -> str:
    return f"/etc/puppetlabs/puppet/ssl/certs/{config.certname}.pem"


def puppet_key(config: ProxmoxConfig) -> str:
    return f"/etc/puppetlabs/puppet/ssl/private_keys/{config.certname}.pem"


def read_vault_secret(config: ProxmoxConfig, runner: Runner) -> str:
    env = os.environ.copy()
    env.setdefault("VAULT_ADDR", config.vault_addr)
    env.setdefault("VAULT_CACERT", config.vault_cacert)
    command = [
        "vault",
        "kv",
        "get",
        f"-field={config.vault_field}",
        config.vault_path,
    ]
    result = runner.run(command, env=env, check=False)
    if result.returncode != 0:
        login = runner.run(
            [
                "vault",
                "login",
                f"-client-cert={puppet_cert(config)}",
                f"-client-key={puppet_key(config)}",
                "-method=cert",
                "-format=json",
                f"name={config.vault_cert_role}",
            ],
            env=env,
        )
        env["VAULT_TOKEN"] = json.loads(login.stdout)["auth"]["client_token"]
        result = runner.run(command, env=env)

    secret = result.stdout.strip()
    if not secret:
        raise RuntimeError("Vault secret is empty")
    return secret


def wake_and_unlock(config: ProxmoxConfig, runner: Runner, password: str) -> None:
    runner.run(["wakeonlan", "-i", config.broadcast, config.mac])
    wait_for_port(runner, config.dropbear_host, 2222, "Dropbear SSH", 120, 2)
    unlock_zfs(config, runner, password)
    wait_for_port(runner, config.proxmox_host, 22, "Proxmox SSH", 300, 5)


def unlock_zfs(config: ProxmoxConfig, runner: Runner, password: str) -> None:
    expect_script = textwrap.dedent(
        f"""
        log_user 0
        set timeout 20
        spawn ssh -p 2222 -o StrictHostKeyChecking=accept-new {config.dropbear_host}
        expect {{
            -re "password for rpool/ROOT|Enter the password.*exit\\\\." {{
                send "$env(SERVER_PASS)\\r"
                exp_continue
            }}
            -re "Unlocking complete|Password for .* accepted" {{
                puts "\\nUnlock detected."
                exp_continue
            }}
            -re "Wrong password|Key load error|encryption failure" {{
                puts "\\nUnlock password was rejected."
                exit 1
            }}
            timeout {{
                puts "\\nUnlock timed out."
                exit 1
            }}
            eof {{
                exit 0
            }}
        }}
        """
    )
    env = os.environ.copy()
    env["SERVER_PASS"] = password
    runner.run(
        ["expect", "-"],
        input_text=expect_script,
        env=env,
        timeout=60,
        capture_output=False,
    )


def proxmox_ssh(
    config: ProxmoxConfig,
    runner: Runner,
    remote_commands: Sequence[str],
    *,
    timeout: int | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return runner.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            config.proxmox_host,
            " && ".join(remote_commands),
        ],
        timeout=timeout,
        check=check,
    )


def port_open(runner: Runner, host: str, port: int, *, timeout: int = 1) -> bool:
    result = runner.run(
        ["nc", "-z", "-w", str(timeout), host, str(port)],
        timeout=timeout + 1,
        check=False,
    )
    return result.returncode == 0


def wait_for_port(
    runner: Runner,
    host: str,
    port: int,
    label: str,
    timeout: int,
    sleep_interval: int,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_open(runner, host, port):
            print(f"{label} is reachable on {host}:{port}")
            return
        time.sleep(sleep_interval)
    raise RuntimeError(f"timed out waiting for {label} on {host}:{port}")


def acquire_lock(path: str, active_message: str) -> object:
    lock = open(path, "w", encoding="utf-8")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        lock.close()
        raise RuntimeError(active_message) from error
    return lock
