#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import json
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
    vault_cert_role: str = "puppet"
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
            password = proxmox_orchestration.read_vault_secret(self.config, self.runner)
            self.initially_on = proxmox_orchestration.port_open(
                self.runner, self.config.proxmox_host, 22, timeout=2
            )

            if not self.initially_on:
                proxmox_orchestration.wake_and_unlock(
                    self.config, self.runner, password
                )
                self.woke_host = True
                self.reliable_ssh = True
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
        proxmox_orchestration.wait_for_port(
            self.runner, self.config.pbs_host, 8007, "PBS API", 300, 5
        )
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            result = self.runner.run(
                [
                    "curl",
                    "--silent",
                    "--show-error",
                    "--output",
                    "/dev/null",
                    "--insecure",
                    f"https://{self.config.pbs_host}:8007/",
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
            timeout=0,
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
        if not upid:
            return
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
        return proxmox_orchestration.proxmox_ssh(
            self.config,
            self.runner,
            remote_commands,
            timeout=timeout,
            check=check,
        )


def parse_upid(output: str) -> str:
    stripped = output.strip()
    if not stripped:
        return ""
    for line in stripped.splitlines():
        if line.startswith("UPID:"):
            return line
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
        lock = proxmox_orchestration.acquire_lock(
            config.lock_file, "another chronicle backup run is already active"
        )
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 75
    with lock:
        return Orchestrator(config, Runner(config)).run()


if __name__ == "__main__":
    raise SystemExit(main())
