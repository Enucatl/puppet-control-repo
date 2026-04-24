#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import fcntl
import json
import os
import subprocess
import sys
import textwrap
import time
from collections.abc import Sequence


@dataclasses.dataclass(frozen=True)
class Config:
    pbs_host: str = "chronicle.home.arpa"
    dropbear_host: str = "dropbear.proxmox-cortex.home.arpa"
    proxmox_host: str = "proxmox-cortex.home.arpa"
    pve_node: str = "proxmox"
    mac: str = "30:56:0f:5e:a9:de"
    broadcast: str = "10.0.0.255"
    ct_id: int = 110
    storage_id: str = "chronicle"
    backup_job_id: str = "pbs-chronicle-weekly"
    vault_path: str = "kv/puppet"
    vault_field: str = "proxmox-cortex"
    vault_addr: str = "https://hcv.home.arpa:8200"
    vault_cacert: str = "/etc/ssl/certs/ca-certificates.crt"
    vault_cert_role: str = "proxmox-puppet"
    certname: str = "proxmox.home.arpa"
    lock_file: str = "/run/chronicle-backup.lock"
    command_timeout: int = 60
    dry_run: bool = False

    @property
    def puppet_cert(self) -> str:
        return f"/etc/puppetlabs/puppet/ssl/certs/{self.certname}.pem"

    @property
    def puppet_key(self) -> str:
        return f"/etc/puppetlabs/puppet/ssl/private_keys/{self.certname}.pem"


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
    def __init__(self, config: Config) -> None:
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
    ) -> subprocess.CompletedProcess[str]:
        print(f"+ {' '.join(command)}", flush=True)
        if self.config.dry_run:
            return self.dry_run_result(command)
        result = subprocess.run(
            list(command),
            input=input_text,
            text=True,
            capture_output=True,
            env=env,
            timeout=timeout or self.config.command_timeout,
            check=False,
        )
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
        if "pct status" in command_text:
            return subprocess.CompletedProcess(command, 0, "status: stopped\n", "")
        if command[:2] == ["pvesh", "create"]:
            return subprocess.CompletedProcess(
                command,
                0,
                '"UPID:proxmox:dry:run:task:vzdump::root@pam:"\n',
                "",
            )
        if command[:3] == [
            "pvesh",
            "get",
            f"/cluster/backup/{self.config.backup_job_id}",
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


class Orchestrator:
    def __init__(self, config: Config, runner: Runner) -> None:
        self.config = config
        self.runner = runner
        self.initially_on = False
        self.woke_host = False
        self.reliable_ssh = False
        self.storage_enable_attempted = False

    def run(self) -> int:
        try:
            password = self.read_vault_secret()
            self.initially_on = self.port_open(self.config.proxmox_host, 22, timeout=2)

            if not self.initially_on:
                self.wake_and_unlock(password)
            else:
                print("proxmox-cortex SSH is already reachable; skipping wake/unlock")
                self.reliable_ssh = True

            self.ensure_container_running()
            self.wait_for_pbs()
            self.enable_storage()
            upid = self.run_backup_job()
            self.wait_for_task(upid)
            return 0
        except Exception as error:
            print(f"chronicle backup failed: {error}", file=sys.stderr)
            return 1
        finally:
            self.cleanup()

    def read_vault_secret(self) -> str:
        env = os.environ.copy()
        env.setdefault("VAULT_ADDR", self.config.vault_addr)
        env.setdefault("VAULT_CACERT", self.config.vault_cacert)
        command = [
            "vault",
            "kv",
            "get",
            f"-field={self.config.vault_field}",
            self.config.vault_path,
        ]
        result = self.runner.run(command, env=env, check=False)
        if result.returncode != 0:
            login = self.runner.run(
                [
                    "vault",
                    "login",
                    "-method=cert",
                    "-format=json",
                    f"name={self.config.vault_cert_role}",
                    f"-client-cert={self.config.puppet_cert}",
                    f"-client-key={self.config.puppet_key}",
                ],
                env=env,
            )
            env["VAULT_TOKEN"] = json.loads(login.stdout)["auth"]["client_token"]
            result = self.runner.run(command, env=env)

        secret = result.stdout.strip()
        if not secret:
            raise RuntimeError("Vault secret is empty")
        return secret

    def wake_and_unlock(self, password: str) -> None:
        self.runner.run(["wakeonlan", "-i", self.config.broadcast, self.config.mac])
        self.woke_host = True
        self.wait_for_port(self.config.dropbear_host, 2222, "Dropbear SSH", 120, 2)
        self.unlock_zfs(password)
        self.wait_for_port(self.config.proxmox_host, 22, "Proxmox SSH", 300, 5)
        self.reliable_ssh = True

    def unlock_zfs(self, password: str) -> None:
        expect_script = textwrap.dedent(
            f"""
            log_user 1
            set timeout 20
            spawn ssh -p 2222 -o StrictHostKeyChecking=accept-new {self.config.dropbear_host}
            expect {{
                "password for rpool/ROOT" {{
                    send "$env(SERVER_PASS)\\r"
                    exp_continue
                }}
                "Unlocking complete" {{
                    puts "\\nUnlock detected."
                    exp_continue
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
        self.runner.run(["expect", "-"], input_text=expect_script, env=env, timeout=60)

    def ensure_container_running(self) -> None:
        status = self.ssh(
            [f"pct status {self.config.ct_id}"],
            check=False,
        )
        if "status: running" in status.stdout:
            print(f"CT {self.config.ct_id} is already running")
            return
        self.ssh([f"pct start {self.config.ct_id}"], timeout=120)

    def wait_for_pbs(self) -> None:
        self.wait_for_port(self.config.pbs_host, 8007, "PBS API", 300, 5)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            result = self.runner.run(
                [
                    "curl",
                    "--fail",
                    "--silent",
                    "--show-error",
                    "--insecure",
                    f"https://{self.config.pbs_host}:8007/api2/json/version",
                ],
                timeout=10,
                check=False,
            )
            if result.returncode == 0:
                return
            time.sleep(5)
        raise RuntimeError(f"timed out waiting for PBS API on {self.config.pbs_host}")

    def enable_storage(self) -> None:
        self.storage_enable_attempted = True
        self.runner.run(["pvesm", "set", self.config.storage_id, "--disable", "0"])

    def run_backup_job(self) -> str:
        job = self.read_backup_job()
        command = [
            "pvesh",
            "create",
            f"/nodes/{self.config.pve_node}/vzdump",
            "--job-id",
            self.config.backup_job_id,
            "--output-format",
            "json",
        ]
        for key in (
            "all",
            "compress",
            "mode",
            "notes-template",
            "notification-mode",
            "storage",
            "prune-backups",
            "vmid",
            "exclude",
            "pool",
        ):
            if key in job:
                command.extend([f"--{key}", format_pvesh_value(job[key])])

        result = self.runner.run(
            command,
            timeout=120,
        )
        return parse_upid(result.stdout)

    def read_backup_job(self) -> dict[str, object]:
        result = self.runner.run(
            [
                "pvesh",
                "get",
                f"/cluster/backup/{self.config.backup_job_id}",
                "--output-format",
                "json",
            ],
            timeout=120,
        )
        parsed = json.loads(result.stdout)
        if not isinstance(parsed, dict):
            raise RuntimeError(
                f"backup job {self.config.backup_job_id} is not an object"
            )
        return parsed

    def wait_for_task(self, upid: str) -> None:
        node = upid.split(":", 2)[1]
        while True:
            result = self.runner.run(
                [
                    "pvesh",
                    "get",
                    f"/nodes/{node}/tasks/{upid}/status",
                    "--output-format",
                    "json",
                ],
                timeout=120,
            )
            status = json.loads(result.stdout)
            if status.get("status") != "stopped":
                time.sleep(15)
                continue
            if status.get("exitstatus") == "OK":
                return
            raise RuntimeError(
                f"backup task {upid} exited with {status.get('exitstatus')}"
            )

    def cleanup(self) -> None:
        if self.storage_enable_attempted:
            try:
                self.runner.run(
                    ["pvesm", "set", self.config.storage_id, "--disable", "1"],
                    check=False,
                )
            except Exception as error:
                print(f"storage disable cleanup failed: {error}", file=sys.stderr)

        if self.woke_host and self.reliable_ssh:
            try:
                self.ssh(["shutdown -h now 'Chronicle backup complete'"], check=False)
            except Exception as error:
                print(f"shutdown cleanup failed: {error}", file=sys.stderr)

    def ssh(
        self,
        remote_commands: Sequence[str],
        *,
        timeout: int | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return self.runner.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                self.config.proxmox_host,
                " && ".join(remote_commands),
            ],
            timeout=timeout,
            check=check,
        )

    def port_open(self, host: str, port: int, *, timeout: int = 1) -> bool:
        result = self.runner.run(
            ["nc", "-z", "-w", str(timeout), host, str(port)],
            timeout=timeout + 1,
            check=False,
        )
        return result.returncode == 0

    def wait_for_port(
        self,
        host: str,
        port: int,
        label: str,
        timeout: int,
        sleep_interval: int,
    ) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.port_open(host, port):
                print(f"{label} is reachable on {host}:{port}")
                return
            time.sleep(sleep_interval)
        raise RuntimeError(f"timed out waiting for {label} on {host}:{port}")


def parse_upid(output: str) -> str:
    stripped = output.strip()
    if stripped.startswith("UPID:"):
        return stripped
    parsed = json.loads(stripped)
    if isinstance(parsed, str):
        return parsed
    if isinstance(parsed, dict) and isinstance(parsed.get("upid"), str):
        return parsed["upid"]
    raise RuntimeError(f"could not parse UPID from pvesh output: {stripped}")


def format_pvesh_value(value: object) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            parts.append(f"{key}={format_pvesh_value(item)}")
        return ",".join(parts)
    raise RuntimeError(f"unsupported pvesh value: {value!r}")


def acquire_lock(path: str) -> object:
    lock = open(path, "w", encoding="utf-8")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise RuntimeError("another chronicle backup run is already active") from error
    return lock


def parse_args(argv: Sequence[str]) -> Config:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pbs-host", default=Config.pbs_host)
    parser.add_argument("--dropbear-host", default=Config.dropbear_host)
    parser.add_argument("--proxmox-host", default=Config.proxmox_host)
    parser.add_argument("--pve-node", default=Config.pve_node)
    parser.add_argument("--mac", default=Config.mac)
    parser.add_argument("--broadcast", default=Config.broadcast)
    parser.add_argument("--ct-id", type=int, default=Config.ct_id)
    parser.add_argument("--storage-id", default=Config.storage_id)
    parser.add_argument("--backup-job-id", default=Config.backup_job_id)
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
        lock = acquire_lock(config.lock_file)
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 75
    with lock:
        return Orchestrator(config, Runner(config)).run()


if __name__ == "__main__":
    raise SystemExit(main())
