#!/usr/bin/env python3

import dataclasses
import fcntl
import html
import json
import os
import secrets
import shlex
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import niquests
from transitions import Machine


@dataclasses.dataclass(frozen=True)
class Config:
    bind_host: str = "0.0.0.0"
    bind_port: int = 8000
    allowed_group: str = "wolf-operators"
    max_session_seconds: int = 14_400
    dropbear_host: str = "dropbear.proxmox-cortex.home.arpa"
    proxmox_host: str = "proxmox-cortex.home.arpa"
    proxmox_api_base_url: str = "https://proxmox-cortex.home.arpa:8006/api2/json"
    proxmox_api_cacert: str = "/etc/ssl/certs/ca-certificates.crt"
    proxmox_api_node: str = "proxmox-cortex"
    guest_host: str = "complex.home.arpa"
    guest_user: str = "user"
    vm_id: int = 200
    mac: str = "30:56:0f:5e:a9:de"
    broadcast: str = "10.0.0.255"
    compose_dir: str = "/opt/docker/wolf"
    dropbear_key: str = "/run/secrets/wolf_dropbear_key"
    dropbear_known_hosts: str = "/run/secrets/wolf_dropbear_known_hosts"
    vault_path: str = "kv/wolf"
    vault_field: str = "proxmox-cortex"
    vault_api_token_field: str = "proxmox-cortex-api-token"
    vault_addr: str = "https://hcv.home.arpa:8200"
    vault_cacert: str = "/etc/ssl/certs/ca-certificates.crt"
    vault_cert_role: str = "wolf"
    vault_client_cert: str = "/run/secrets/wolf_cert"
    vault_client_key: str = "/run/secrets/wolf_key"
    lock_file: str = "/run/wolf/session.lock"
    state_file: str = "/state/session.json"
    trusted_proxy_hosts: str = "traefik"
    command_timeout: int = 60
    dry_run: bool = False


@dataclasses.dataclass
class SessionState:
    phase: str = "idle"
    started_by: str | None = None
    started_at: float | None = None
    deadline: float | None = None
    last_action: str | None = None
    last_result: str | None = None
    last_stop_reason: str | None = None


@dataclasses.dataclass(frozen=True)
class Observation:
    host: str
    vm: str
    guest: str
    wolf: str
    checked_at: float

    def as_dict(self) -> dict[str, str | float]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class AuditContext:
    user: str
    source_ip: str
    request_id: str
    action: str


class CommandError(RuntimeError):
    def __init__(
        self, command: Sequence[str], result: subprocess.CompletedProcess[str]
    ) -> None:
        self.command = command
        self.result = result
        super().__init__(f"{' '.join(command)} failed with exit {result.returncode}")


class Runner:
    def __init__(self, config: Config) -> None:
        self.config = config

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
        if self.config.dry_run:
            return self.dry_run_result(command)
        kwargs: dict[str, object] = {
            "input": input_text,
            "text": True,
            "env": env,
            "timeout": self.config.command_timeout if timeout is None else timeout,
            "check": False,
        }
        if capture_output:
            kwargs["capture_output"] = True
        result = subprocess.run(list(command), **kwargs)
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
            return subprocess.CompletedProcess(command, 0, "", "")
        if "qm status" in command_text:
            return subprocess.CompletedProcess(command, 0, "status: stopped\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")


class HttpClient:
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        data: Sequence[tuple[str, str]] | None = None,
        timeout: int | None = None,
        verify: str | bool = True,
    ) -> subprocess.CompletedProcess[str]:
        response = niquests.request(
            method,
            url,
            headers=headers,
            data=list(data) if data is not None else None,
            timeout=timeout,
            verify=verify,
        )
        response.raise_for_status()
        return subprocess.CompletedProcess([method, url], 0, response.text, "")


class WolfController:
    PHASES = ["idle", "starting", "running", "stopping", "failed"]
    TRANSITIONS = [
        {"trigger": "begin_start", "source": ["idle", "failed"], "dest": "starting"},
        {"trigger": "finish_start", "source": "starting", "dest": "running"},
        {
            "trigger": "begin_stop",
            "source": ["idle", "running", "failed"],
            "dest": "stopping",
        },
        {"trigger": "finish_stop", "source": "stopping", "dest": "idle"},
        {"trigger": "fail", "source": ["starting", "stopping"], "dest": "failed"},
        {"trigger": "lose", "source": "running", "dest": "failed"},
    ]

    def __init__(
        self, config: Config, runner: Runner, http_client: HttpClient | None = None
    ) -> None:
        self.config = config
        self.runner = runner
        self.http_client = HttpClient() if http_client is None else http_client
        self.state = self.load_state()
        self._proxmox_api_token: str | None = None
        self.csrf_token = secrets.token_urlsafe(32)
        self.machine = Machine(
            model=self.state,
            states=self.PHASES,
            transitions=self.TRANSITIONS,
            initial=self.state.phase,
            model_attribute="phase",
            auto_transitions=False,
        )
        self.state_lock = threading.Lock()
        self.timeout_timer: threading.Timer | None = None
        self.restore_timeout()

    def load_state(self) -> SessionState:
        path = Path(self.config.state_file)
        if not path.exists():
            return SessionState()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except OSError, json.JSONDecodeError:
            return SessionState(phase="failed", last_result="state-load-failed")
        if not isinstance(payload, dict):
            return SessionState(phase="failed", last_result="state-load-failed")
        fields = {field.name for field in dataclasses.fields(SessionState)}
        values = {key: value for key, value in payload.items() if key in fields}
        state = SessionState(**values)
        if state.phase not in self.PHASES:
            state.phase = "failed"
            state.last_result = "state-load-failed"
        if state.phase in {"starting", "stopping"}:
            state.phase = "failed"
            state.last_result = "interrupted"
        return state

    def restore_timeout(self) -> None:
        if self.state.phase != "running" or self.state.deadline is None:
            return
        delay = max(1, self.state.deadline - time.time())
        self.schedule_timeout(delay)

    def save_state_locked(self) -> None:
        path = Path(self.config.state_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(f"{path.suffix}.{os.getpid()}.tmp")
        tmp_path.write_text(
            json.dumps(dataclasses.asdict(self.state)), encoding="utf-8"
        )
        os.replace(tmp_path, path)

    def status(self) -> dict[str, Any]:
        observation = self.observe()
        with self.state_lock:
            self.reconcile_locked(observation)
            remaining = (
                max(0, int(self.state.deadline - time.time()))
                if self.state.deadline is not None
                else 0
            )
            active = (
                self.state.phase in {"starting", "running", "stopping"}
                or observation.wolf == "running"
            )
            return {
                "phase": self.state.phase,
                "active": active,
                "ownership": self.ownership_locked(observation),
                "observed": observation.as_dict(),
                "started_by": self.state.started_by,
                "started_at": self.state.started_at,
                "timeout_remaining_seconds": remaining,
                "last_action": self.state.last_action,
                "last_result": self.state.last_result,
                "last_stop_reason": self.state.last_stop_reason,
            }

    def start_session(
        self, user: str, audit: AuditContext | None = None
    ) -> dict[str, Any]:
        with acquire_lock(self.config.lock_file):
            observation = self.observe_step(audit)
            with self.state_lock:
                self.reconcile_locked(observation)
                if self.state.phase in {"starting", "running", "stopping"}:
                    already_active = True
                else:
                    already_active = False
            if already_active:
                self.log_step(
                    audit,
                    "start-decision",
                    "skipped",
                    reason="already-active",
                    phase=self.state.phase,
                )
                return self.status()
            if observation.wolf == "running":
                self.log_step(
                    audit,
                    "start-decision",
                    "adopted",
                    reason="wolf-already-running",
                )
                deadline = time.time() + self.config.max_session_seconds
                with self.state_lock:
                    self.state.phase = "running"
                    self.state.started_by = user
                    self.state.started_at = time.time()
                    self.state.deadline = deadline
                    self.state.last_action = "start"
                    self.state.last_result = "ok"
                    self.state.last_stop_reason = None
                    self.save_state_locked()
                self.schedule_timeout(self.config.max_session_seconds)
                return self.status()

            self.log_step(audit, "state-transition", "starting", phase="starting")
            with self.state_lock:
                self.state.begin_start()
                self.state.last_action = "start"
                self.state.last_result = "running"
                self.save_state_locked()

            started_host = False
            started_vm = False
            compose_attempted = False
            try:
                host_reachable = self.run_step(
                    audit,
                    "check-proxmox-api",
                    lambda: self.port_open(self.config.proxmox_host, 8006, timeout=2),
                    host=self.config.proxmox_host,
                    port=8006,
                )
                if not host_reachable:
                    self.log_step(
                        audit,
                        "check-proxmox-api",
                        "stopped",
                        host=self.config.proxmox_host,
                        port=8006,
                    )
                    password = self.run_step(
                        audit,
                        "read-vault-unlock-secret",
                        self.read_vault_secret,
                        vault_path=self.config.vault_path,
                        vault_field=self.config.vault_field,
                    )
                    self.wake_and_unlock(password, audit)
                    started_host = True
                else:
                    self.log_step(
                        audit,
                        "wake-and-unlock",
                        "skipped",
                        reason="proxmox-api-reachable",
                    )

                vm_running = self.run_step(
                    audit,
                    "check-vm",
                    self.vm_running,
                    vm_id=self.config.vm_id,
                )
                if not vm_running:
                    self.run_step(
                        audit,
                        "start-vm",
                        lambda: self.proxmox_api_post(
                            f"/nodes/{self.config.proxmox_api_node}/qemu/{self.config.vm_id}/status/start",
                            timeout=120,
                        ),
                        vm_id=self.config.vm_id,
                    )
                    started_vm = True
                else:
                    self.log_step(
                        audit,
                        "start-vm",
                        "skipped",
                        reason="vm-already-running",
                        vm_id=self.config.vm_id,
                    )

                self.run_step(
                    audit,
                    "wait-guest-ssh",
                    lambda: self.wait_for_port(
                        self.config.guest_host, 22, "Wolf guest SSH", 300, 5
                    ),
                    host=self.config.guest_host,
                    port=22,
                    timeout_seconds=300,
                )
                compose_attempted = True
                self.run_step(
                    audit,
                    "compose-up",
                    lambda: self.guest_exec(
                        f"cd {shlex.quote(self.config.compose_dir)} && docker compose up -d",
                        timeout=300,
                    ),
                    compose_dir=self.config.compose_dir,
                )
            except Exception:
                self.log_step(audit, "start-session", "failed")
                self.cleanup_failed_start(
                    started_host, started_vm, compose_attempted, audit
                )
                with self.state_lock:
                    self.state.fail()
                    self.state.last_result = "failed"
                    self.save_state_locked()
                raise

            deadline = time.time() + self.config.max_session_seconds
            self.log_step(audit, "state-transition", "running", phase="running")
            with self.state_lock:
                self.state.finish_start()
                self.state.started_by = user
                self.state.started_at = time.time()
                self.state.deadline = deadline
                self.state.last_result = "ok"
                self.state.last_stop_reason = None
                self.save_state_locked()
            self.schedule_timeout(self.config.max_session_seconds)
            return self.status()

    def stop_session(
        self, reason: str, audit: AuditContext | None = None
    ) -> dict[str, Any]:
        with acquire_lock(self.config.lock_file):
            self.cancel_timeout()
            observation = self.observe_step(audit)
            with self.state_lock:
                self.reconcile_locked(observation)
            if self.state.phase in {"starting", "stopping"}:
                self.log_step(
                    audit,
                    "stop-decision",
                    "skipped",
                    reason="operation-in-progress",
                    phase=self.state.phase,
                )
                return self.status()
            self.log_step(audit, "state-transition", "stopping", phase="stopping")
            with self.state_lock:
                self.state.begin_stop()
                self.state.last_action = "stop"
                self.state.last_result = "running"
                was_active = self.state.started_at is not None
                self.save_state_locked()

            errors: list[str] = []
            if observation.host == "running":
                actions = [
                    (
                        "compose down",
                        "compose-down",
                        lambda: self.guest_exec(self.compose_down(), timeout=300),
                    ),
                    (
                        "VM shutdown",
                        "shutdown-vm",
                        lambda: self.proxmox_api_post(
                            f"/nodes/{self.config.proxmox_api_node}/qemu/{self.config.vm_id}/status/shutdown",
                            check=False,
                            timeout=300,
                        ),
                    ),
                    (
                        "host shutdown",
                        "shutdown-host",
                        lambda: self.proxmox_api_post(
                            f"/nodes/{self.config.proxmox_api_node}/status",
                            data=[("command", "shutdown")],
                            check=False,
                            timeout=300,
                        ),
                    ),
                ]
                for label, step, callback in actions:
                    try:
                        result = self.run_step(audit, step, callback)
                        if isinstance(result, subprocess.CompletedProcess):
                            error = result.stderr.strip() or result.stdout.strip()
                            if result.returncode != 0:
                                errors.append(f"{label}: {error}")
                    except Exception as error:
                        errors.append(f"{label}: {error}")
            elif was_active:
                self.log_step(
                    audit,
                    "stop-decision",
                    "failed",
                    reason="proxmox-api-unreachable",
                )
                errors.append("Proxmox API is not reachable")

            with self.state_lock:
                self.state.deadline = None
                self.state.last_stop_reason = reason
                self.state.last_result = "failed" if errors else "ok"
                if errors:
                    self.state.fail()
                else:
                    self.state.finish_stop()
                    self.state.started_by = None
                    self.state.started_at = None
                self.save_state_locked()
            if errors:
                self.log_step(audit, "stop-session", "failed")
                raise RuntimeError("; ".join(errors))
            self.log_step(audit, "state-transition", "idle", phase="idle")
            return self.status()

    def observe(self) -> Observation:
        host = (
            "running"
            if self.port_open(self.config.proxmox_host, 8006, timeout=2)
            else "stopped"
        )
        if host != "running":
            return Observation(
                host=host,
                vm="unknown",
                guest="unknown",
                wolf="unknown",
                checked_at=time.time(),
            )

        vm = "running" if self.vm_running() else "stopped"
        guest = "unknown"
        wolf = "unknown"
        if vm == "running":
            guest = (
                "running"
                if self.port_open(self.config.guest_host, 22, timeout=1)
                else "stopped"
            )
            result = self.guest_exec(self.compose_ps_command(), check=False)
            if result.returncode == 0:
                wolf_output = guest_command_output(result.stdout)
                wolf = "running" if wolf_output.strip() else "stopped"

        return Observation(
            host=host,
            vm=vm,
            guest=guest,
            wolf=wolf,
            checked_at=time.time(),
        )

    def reconcile_locked(self, observation: Observation) -> None:
        if self.state.phase != "running":
            return
        session_missing = (
            observation.host != "running"
            or observation.vm == "stopped"
            or observation.guest == "stopped"
            or observation.wolf == "stopped"
        )
        if not session_missing:
            return
        self.cancel_timeout()
        self.state.deadline = None
        self.state.last_result = "lost"
        self.state.last_stop_reason = "external"
        self.state.lose()
        self.save_state_locked()

    def ownership_locked(self, observation: Observation) -> str:
        if self.state.phase in {"starting", "running", "stopping"}:
            return "app"
        if observation.wolf == "running":
            return "external"
        if observation.wolf == "unknown":
            return "unknown"
        return "none"

    def cleanup_failed_start(
        self,
        started_host: bool,
        started_vm: bool,
        compose_attempted: bool,
        audit: AuditContext | None = None,
    ) -> None:
        if compose_attempted:
            try:
                self.run_step(
                    audit,
                    "cleanup-compose-down",
                    lambda: self.guest_exec(
                        self.compose_down(), timeout=300, check=False
                    ),
                )
            except Exception as error:
                self.log_step(
                    audit,
                    "cleanup-compose-down",
                    "failed",
                    error=str(error),
                )
                print(f"startup cleanup compose down failed: {error}", file=sys.stderr)
        if started_vm:
            try:
                self.run_step(
                    audit,
                    "cleanup-vm-shutdown",
                    lambda: self.proxmox_api_post(
                        f"/nodes/{self.config.proxmox_api_node}/qemu/{self.config.vm_id}/status/shutdown",
                        check=False,
                        timeout=300,
                    ),
                )
            except Exception as error:
                self.log_step(
                    audit,
                    "cleanup-vm-shutdown",
                    "failed",
                    error=str(error),
                )
                print(f"startup cleanup VM shutdown failed: {error}", file=sys.stderr)
        if started_host:
            try:
                self.run_step(
                    audit,
                    "cleanup-host-shutdown",
                    lambda: self.proxmox_api_post(
                        f"/nodes/{self.config.proxmox_api_node}/status",
                        data=[("command", "shutdown")],
                        check=False,
                        timeout=300,
                    ),
                )
            except Exception as error:
                self.log_step(
                    audit,
                    "cleanup-host-shutdown",
                    "failed",
                    error=str(error),
                )
                print(f"startup cleanup host shutdown failed: {error}", file=sys.stderr)

    def schedule_timeout(self, delay: float | None = None) -> None:
        self.cancel_timeout()
        self.timeout_timer = threading.Timer(
            self.config.max_session_seconds if delay is None else delay,
            self.timeout_session,
        )
        self.timeout_timer.daemon = True
        self.timeout_timer.start()

    def cancel_timeout(self) -> None:
        if self.timeout_timer is not None:
            self.timeout_timer.cancel()
            self.timeout_timer = None

    def timeout_session(self) -> None:
        try:
            log_event(
                "system",
                "-",
                str(uuid.uuid4()),
                "stop",
                "timeout-started",
                reason="timeout",
            )
            self.stop_session("timeout")
            log_event("system", "-", str(uuid.uuid4()), "stop", "ok", reason="timeout")
        except Exception as error:
            log_event(
                "system",
                "-",
                str(uuid.uuid4()),
                "stop",
                "failed",
                reason="timeout",
                error=str(error),
            )

    def read_vault_secret(self, field: str | None = None) -> str:
        env = os.environ.copy()
        env.setdefault("VAULT_ADDR", self.config.vault_addr)
        env.setdefault("VAULT_CACERT", self.config.vault_cacert)
        field_name = self.config.vault_field if field is None else field
        command = [
            "vault",
            "kv",
            "get",
            f"-field={field_name}",
            self.config.vault_path,
        ]
        result = self.runner.run(command, env=env, check=False)
        if result.returncode != 0:
            login = self.runner.run(
                [
                    "vault",
                    "login",
                    "-no-store",
                    f"-client-cert={self.config.vault_client_cert}",
                    f"-client-key={self.config.vault_client_key}",
                    "-method=cert",
                    "-format=json",
                    f"name={self.config.vault_cert_role}",
                ],
                env=env,
            )
            env["VAULT_TOKEN"] = json.loads(login.stdout)["auth"]["client_token"]
            result = self.runner.run(command, env=env)
        secret = result.stdout.strip()
        if not secret:
            raise RuntimeError("Vault secret is empty")
        return secret

    def wake_and_unlock(self, password: str, audit: AuditContext | None = None) -> None:
        self.run_step(
            audit,
            "send-wake-on-lan",
            lambda: self.runner.run(
                ["wakeonlan", "-i", self.config.broadcast, self.config.mac]
            ),
            broadcast=self.config.broadcast,
            mac=self.config.mac,
        )
        self.run_step(
            audit,
            "wait-dropbear-ssh",
            lambda: self.wait_for_port(
                self.config.dropbear_host, 2222, "Dropbear SSH", 120, 2
            ),
            host=self.config.dropbear_host,
            port=2222,
            timeout_seconds=120,
        )
        self.run_step(audit, "unlock-zfs", lambda: self.unlock_zfs(password))
        self.run_step(
            audit,
            "wait-proxmox-api",
            lambda: self.wait_for_port(
                self.config.proxmox_host, 8006, "Proxmox API", 300, 5
            ),
            host=self.config.proxmox_host,
            port=8006,
            timeout_seconds=300,
        )

    def unlock_zfs(self, password: str) -> None:
        expect_script = f"""
        log_user 0
        set timeout 20
        spawn ssh -i {shlex.quote(self.config.dropbear_key)} -p 2222 -o UserKnownHostsFile={shlex.quote(self.config.dropbear_known_hosts)} -o StrictHostKeyChecking=yes root@{self.config.dropbear_host}
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
        env = os.environ.copy()
        env["SERVER_PASS"] = password
        self.runner.run(
            ["expect", "-"],
            input_text=expect_script,
            env=env,
            timeout=60,
            capture_output=False,
        )

    def vm_running(self) -> bool:
        result = self.proxmox_api_get(
            f"/nodes/{self.config.proxmox_api_node}/qemu/{self.config.vm_id}/status/current",
            check=False,
        )
        if result.returncode != 0:
            return False
        payload = proxmox_api_data(result.stdout)
        status = payload.get("status") if isinstance(payload, dict) else None
        return status == "running"

    def compose_ps_command(self) -> str:
        return (
            f"cd {shlex.quote(self.config.compose_dir)} && "
            "docker compose ps --services --filter status=running"
        )

    def compose_down(self) -> str:
        return f"cd {shlex.quote(self.config.compose_dir)} && docker compose down"

    def guest_exec(
        self, command: str, *, timeout: int | None = None, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        result = self.proxmox_api_post(
            f"/nodes/{self.config.proxmox_api_node}/qemu/{self.config.vm_id}/agent/exec",
            data=[
                ("command", part)
                for part in [
                    "/bin/su",
                    "-",
                    self.config.guest_user,
                    "-c",
                    command,
                ]
            ],
            timeout=timeout,
            check=check,
        )
        payload = proxmox_api_data(result.stdout)
        pid = payload.get("pid")
        if not isinstance(pid, int):
            return result

        deadline = time.monotonic() + (timeout if timeout is not None else 30)
        while True:
            status = self.proxmox_api_get(
                f"/nodes/{self.config.proxmox_api_node}/qemu/{self.config.vm_id}/agent/exec-status?pid={pid}",
                timeout=timeout,
                check=check,
            )
            status_payload = proxmox_api_data(status.stdout)
            if status_payload.get("exited"):
                exitcode = status_payload.get("exitcode", 1)
                if not isinstance(exitcode, int):
                    exitcode = 1
                completed = subprocess.CompletedProcess(
                    ["guest-exec", command],
                    exitcode,
                    status.stdout,
                    status_payload.get("err-data", ""),
                )
                if check and exitcode != 0:
                    raise CommandError(["guest-exec", command], completed)
                return completed
            if time.monotonic() >= deadline:
                timed_out = subprocess.CompletedProcess(
                    ["guest-exec", command],
                    1,
                    status.stdout,
                    f"guest command timed out after {timeout or 30} seconds",
                )
                if check:
                    raise CommandError(["guest-exec", command], timed_out)
                return timed_out
            time.sleep(1)

    def proxmox_api_get(
        self, path: str, *, timeout: int | None = None, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        return self.proxmox_api_request("GET", path, timeout=timeout, check=check)

    def proxmox_api_post(
        self,
        path: str,
        *,
        data: Sequence[tuple[str, str]] | None = None,
        timeout: int | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return self.proxmox_api_request(
            "POST", path, data=data, timeout=timeout, check=check
        )

    def proxmox_api_request(
        self,
        method: str,
        path: str,
        *,
        data: Sequence[tuple[str, str]] | None = None,
        timeout: int | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        token = self.read_proxmox_api_token()
        try:
            return self.http_client.request(
                method,
                f"{self.config.proxmox_api_base_url}{path}",
                headers={"Authorization": f"PVEAPIToken={token}"},
                data=data,
                timeout=timeout,
                verify=self.config.proxmox_api_cacert,
            )
        except Exception as error:
            result = subprocess.CompletedProcess([method, path], 1, "", str(error))
            if check:
                raise CommandError([method, path], result) from error
            return result

    def read_proxmox_api_token(self) -> str:
        if self._proxmox_api_token is None:
            self._proxmox_api_token = self.read_vault_secret(
                self.config.vault_api_token_field
            )
        return self._proxmox_api_token

    def port_open(self, host: str, port: int, *, timeout: int = 1) -> bool:
        try:
            result = self.runner.run(
                ["nc", "-z", "-w", str(timeout), host, str(port)],
                timeout=timeout + 1,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return False
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
                return
            time.sleep(sleep_interval)
        raise RuntimeError(f"timed out waiting for {label} on {host}:{port}")

    def run_step(
        self,
        audit: AuditContext | None,
        step: str,
        callback: Callable[[], Any],
        **extra: Any,
    ) -> Any:
        started_at = time.monotonic()
        self.log_step(audit, step, "started", **extra)
        try:
            result = callback()
        except Exception as error:
            self.log_step(
                audit,
                step,
                "failed",
                duration_ms=self.duration_ms(started_at),
                error=str(error),
                **extra,
            )
            raise
        if isinstance(result, subprocess.CompletedProcess) and result.returncode != 0:
            self.log_step(
                audit,
                step,
                "failed",
                duration_ms=self.duration_ms(started_at),
                error=result.stderr.strip() or result.stdout.strip(),
                returncode=result.returncode,
                **extra,
            )
            return result
        self.log_step(
            audit,
            step,
            "ok",
            duration_ms=self.duration_ms(started_at),
            **extra,
        )
        return result

    def observe_step(self, audit: AuditContext | None) -> Observation:
        started_at = time.monotonic()
        self.log_step(audit, "observe", "started")
        try:
            observation = self.observe()
        except Exception as error:
            self.log_step(
                audit,
                "observe",
                "failed",
                duration_ms=self.duration_ms(started_at),
                error=str(error),
            )
            raise
        self.log_step(
            audit,
            "observe",
            "ok",
            duration_ms=self.duration_ms(started_at),
            observed=observation.as_dict(),
        )
        return observation

    def log_step(
        self,
        audit: AuditContext | None,
        step: str,
        result: str,
        **extra: Any,
    ) -> None:
        if audit is None:
            return
        log_event(
            audit.user,
            audit.source_ip,
            audit.request_id,
            audit.action,
            result,
            event="step",
            step=step,
            **extra,
        )

    def duration_ms(self, started_at: float) -> int:
        return round((time.monotonic() - started_at) * 1000)


def acquire_lock(path: str) -> object:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    lock = open(path, "w", encoding="utf-8")
    fcntl.flock(lock, fcntl.LOCK_EX)
    return lock


def guest_command_output(stdout: str) -> str:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout
    if not isinstance(payload, dict):
        return stdout
    data = payload.get("data", payload)
    if isinstance(data, dict):
        output = data.get("out-data", data.get("stdout", ""))
    else:
        output = data
    return output if isinstance(output, str) else ""


def proxmox_api_data(stdout: str) -> dict[str, Any]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data", {})
    return data if isinstance(data, dict) else {}


def log_event(
    user: str,
    source_ip: str,
    request_id: str,
    action: str,
    result: str,
    **extra: Any,
) -> None:
    event = {
        "user": user,
        "source_ip": source_ip,
        "request_id": request_id,
        "action": action,
        "result": result,
        **extra,
    }
    print(json.dumps(event, sort_keys=True), flush=True)


def load_config() -> Config:
    return Config(
        bind_host=os.getenv("WOLF_BIND_HOST", Config.bind_host),
        bind_port=int(os.getenv("WOLF_BIND_PORT", str(Config.bind_port))),
        allowed_group=os.getenv("WOLF_ALLOWED_GROUP", Config.allowed_group),
        max_session_seconds=int(
            os.getenv("WOLF_MAX_SESSION_SECONDS", str(Config.max_session_seconds))
        ),
        dropbear_host=os.getenv("WOLF_DROPBEAR_HOST", Config.dropbear_host),
        proxmox_host=os.getenv("WOLF_PROXMOX_HOST", Config.proxmox_host),
        proxmox_api_base_url=os.getenv(
            "WOLF_PROXMOX_API_BASE_URL", Config.proxmox_api_base_url
        ),
        proxmox_api_cacert=os.getenv(
            "WOLF_PROXMOX_API_CACERT", Config.proxmox_api_cacert
        ),
        proxmox_api_node=os.getenv("WOLF_PROXMOX_API_NODE", Config.proxmox_api_node),
        guest_host=os.getenv("WOLF_GUEST_HOST", Config.guest_host),
        guest_user=os.getenv("WOLF_GUEST_USER", Config.guest_user),
        vm_id=int(os.getenv("WOLF_VM_ID", str(Config.vm_id))),
        mac=os.getenv("WOLF_MAC", Config.mac),
        broadcast=os.getenv("WOLF_BROADCAST", Config.broadcast),
        compose_dir=os.getenv("WOLF_COMPOSE_DIR", Config.compose_dir),
        dropbear_key=os.getenv("WOLF_DROPBEAR_KEY", Config.dropbear_key),
        dropbear_known_hosts=os.getenv(
            "WOLF_DROPBEAR_KNOWN_HOSTS", Config.dropbear_known_hosts
        ),
        vault_path=os.getenv("WOLF_VAULT_PATH", Config.vault_path),
        vault_field=os.getenv("WOLF_VAULT_FIELD", Config.vault_field),
        vault_api_token_field=os.getenv(
            "WOLF_VAULT_API_TOKEN_FIELD", Config.vault_api_token_field
        ),
        vault_addr=os.getenv("VAULT_ADDR", Config.vault_addr),
        vault_cacert=os.getenv("VAULT_CACERT", Config.vault_cacert),
        vault_cert_role=os.getenv("WOLF_VAULT_CERT_ROLE", Config.vault_cert_role),
        vault_client_cert=os.getenv("WOLF_VAULT_CLIENT_CERT", Config.vault_client_cert),
        vault_client_key=os.getenv("WOLF_VAULT_CLIENT_KEY", Config.vault_client_key),
        lock_file=os.getenv("WOLF_LOCK_FILE", Config.lock_file),
        state_file=os.getenv("WOLF_STATE_FILE", Config.state_file),
        trusted_proxy_hosts=os.getenv(
            "WOLF_TRUSTED_PROXY_HOSTS", Config.trusted_proxy_hosts
        ),
        command_timeout=int(
            os.getenv("WOLF_COMMAND_TIMEOUT", str(Config.command_timeout))
        ),
        dry_run=os.getenv("WOLF_DRY_RUN", "").lower() in {"1", "true", "yes"},
    )


def make_handler(
    controller: WolfController, config: Config
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "wolf/1.0"

        def do_GET(self) -> None:
            parsed_path = urlparse(self.path)
            if parsed_path.path == "/":
                self.handle_ui()
            elif parsed_path.path == "/healthz":
                self.handle_healthz()
            elif parsed_path.path == "/v1/session/status":
                self.handle_status()
            else:
                self.respond_error(HTTPStatus.NOT_FOUND, "not found")

        def do_POST(self) -> None:
            if self.path == "/v1/session/start":
                self.handle_action("start")
            elif self.path == "/v1/session/stop":
                self.handle_action("stop")
            else:
                self.respond_error(HTTPStatus.NOT_FOUND, "not found")

        def handle_ui(self) -> None:
            auth = self.authorize()
            if auth is None:
                return
            status = controller.status()
            user = html.escape(auth["user"])
            csrf_token = html.escape(controller.csrf_token)
            remaining = status["timeout_remaining_seconds"]
            status_payload = {"user": auth["user"], **status}
            status_json = html.escape(json.dumps(status_payload, indent=2))
            query = parse_qs(urlparse(self.path).query)
            result = query.get("result", [""])[-1]
            error = query.get("error", [""])[-1]
            banner = ""
            if result:
                banner = (
                    f'<p class="banner ok">{html.escape(result.replace("-", " "))}</p>'
                )
            if error:
                banner = (
                    '<p class="banner failed">'
                    "Operation failed. Request "
                    f"{html.escape(error)}"
                    "</p>"
                )
            body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Wolf Power</title>
  <style>
    :root {{ color-scheme: dark; font-family: system-ui, sans-serif; }}
    body {{ margin: 0; min-height: 100vh; background: #101418; color: #f3f7fb; }}
    main {{ width: min(58rem, calc(100vw - 2rem)); margin: 2rem auto; }}
    header {{ display: flex; align-items: center; justify-content: space-between; gap: 1rem; margin-bottom: 1rem; }}
    h1 {{ font-size: 1.35rem; margin: 0; }}
    .actions {{ display: flex; flex-wrap: wrap; gap: .75rem; }}
    .banner {{ margin: 0 0 1rem; padding: .75rem 1rem; border-radius: 6px; font-weight: 700; }}
    .banner.ok {{ background: #143c27; color: #b7f7cf; }}
    .banner.failed {{ background: #451923; color: #fecdd3; }}
    .summary {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .75rem; margin-bottom: 1rem; }}
    .metric {{ background: #182028; border: 1px solid #2d3945; border-radius: 6px; padding: .75rem; }}
    .metric span {{ display: block; color: #a9b7c6; font-size: .8rem; margin-bottom: .35rem; }}
    .metric strong {{ display: block; font-size: 1rem; overflow-wrap: anywhere; }}
    pre {{ margin: 0; padding: 1rem; background: #0b0f13; border: 1px solid #2d3945; border-radius: 6px; overflow: auto; line-height: 1.45; }}
    form {{ margin: 0; }}
    button {{ border: 0; border-radius: 6px; padding: .8rem 1rem; font-weight: 700; cursor: pointer; }}
    button[value=start] {{ background: #4ade80; color: #102015; }}
    button[value=stop] {{ background: #fb7185; color: #2a0d12; }}
    @media (max-width: 720px) {{
      header {{ align-items: stretch; flex-direction: column; }}
      .summary {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>Wolf Power</h1>
      <div class="actions">
        <form method="post" action="/v1/session/start"><input type="hidden" name="csrf_token" value="{csrf_token}"><button name="action" value="start">Start</button></form>
        <form method="post" action="/v1/session/stop"><input type="hidden" name="csrf_token" value="{csrf_token}"><button name="action" value="stop">Stop</button></form>
      </div>
    </header>
    {banner}
    <section class="summary">
      <div class="metric"><span>Phase</span><strong>{html.escape(str(status["phase"]))}</strong></div>
      <div class="metric"><span>Ownership</span><strong>{html.escape(str(status["ownership"]))}</strong></div>
      <div class="metric"><span>Wolf</span><strong>{html.escape(str(status["observed"]["wolf"]))}</strong></div>
      <div class="metric"><span>Timeout</span><strong>{remaining // 60} min</strong></div>
    </section>
    <pre>{status_json}</pre>
  </main>
</body>
</html>
            """
            self.respond(HTTPStatus.OK, body.encode(), "text/html; charset=utf-8")

        def handle_healthz(self) -> None:
            self.respond(HTTPStatus.OK, b"ok\n", "text/plain; charset=utf-8")

        def handle_status(self) -> None:
            auth = self.authorize()
            if auth is None:
                return
            self.respond_json(
                HTTPStatus.OK, {"user": auth["user"], **controller.status()}
            )

        def handle_action(self, action: str) -> None:
            auth = self.authorize()
            if auth is None:
                return
            post_fields = self.post_fields()
            if not self.origin_allowed() or not self.csrf_token_valid(post_fields):
                self.respond_error(HTTPStatus.FORBIDDEN, "forbidden")
                return
            request_id = self.request_id()
            source_ip = self.source_ip()
            audit = AuditContext(auth["user"], source_ip, request_id, action)
            try:
                if action == "start":
                    result = controller.start_session(auth["user"], audit)
                else:
                    result = controller.stop_session("manual", audit)
                log_event(
                    auth["user"],
                    source_ip,
                    request_id,
                    action,
                    "ok",
                    reason=result.get("last_stop_reason") or "",
                )
                if not self.wants_json():
                    self.respond_redirect(f"/?result={action}-ok")
                    return
                self.respond_json(HTTPStatus.OK, {"user": auth["user"], **result})
            except Exception as error:
                log_event(
                    auth["user"],
                    source_ip,
                    request_id,
                    action,
                    "failed",
                    error=str(error),
                )
                if not self.wants_json():
                    self.respond_redirect(f"/?error={request_id}")
                    return
                self.respond_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": "operation failed", "request_id": request_id},
                )

        def authorize(self) -> dict[str, str] | None:
            if not self.trusted_proxy():
                self.respond_error(HTTPStatus.FORBIDDEN, "forbidden")
                return None
            user = self.headers.get("Remote-User", "")
            groups = {
                group.strip()
                for group in self.headers.get("Remote-Groups", "").split(",")
                if group.strip()
            }
            if not user:
                self.respond_error(HTTPStatus.UNAUTHORIZED, "authentication required")
                return None
            if config.allowed_group not in groups:
                self.respond_error(HTTPStatus.FORBIDDEN, "forbidden")
                return None
            return {"user": user}

        def trusted_proxy(self) -> bool:
            source_ip = self.client_address[0]
            hostnames = [
                host.strip()
                for host in config.trusted_proxy_hosts.split(",")
                if host.strip()
            ]
            if not hostnames:
                return True
            for hostname in hostnames:
                try:
                    addresses = {
                        item[4][0]
                        for item in socket.getaddrinfo(
                            hostname, None, type=socket.SOCK_STREAM
                        )
                    }
                except socket.gaierror:
                    continue
                if source_ip in addresses:
                    return True
            return False

        def post_fields(self) -> dict[str, str]:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0:
                return {}
            payload = self.rfile.read(length).decode("utf-8")
            parsed = parse_qs(payload, keep_blank_values=True)
            return {key: values[-1] for key, values in parsed.items() if values}

        def origin_allowed(self) -> bool:
            host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host", "")
            origin = self.headers.get("Origin") or self.headers.get("Referer", "")
            if not host or not origin:
                return False
            parsed = urlparse(origin)
            return parsed.netloc == host

        def csrf_token_valid(self, post_fields: dict[str, str]) -> bool:
            token = self.headers.get("X-Wolf-CSRF-Token") or post_fields.get(
                "csrf_token", ""
            )
            return secrets.compare_digest(token, controller.csrf_token)

        def source_ip(self) -> str:
            forwarded = self.headers.get("X-Forwarded-For", "")
            return forwarded.split(",", 1)[0].strip() or self.client_address[0]

        def request_id(self) -> str:
            return self.headers.get("X-Request-Id", "") or str(uuid.uuid4())

        def wants_json(self) -> bool:
            accept = self.headers.get("Accept", "")
            return "application/json" in accept and "text/html" not in accept

        def respond_redirect(self, location: str) -> None:
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def respond_json(self, status: HTTPStatus, body: dict[str, Any]) -> None:
            self.respond(
                status,
                json.dumps(body).encode(),
                "application/json; charset=utf-8",
            )

        def respond_error(self, status: HTTPStatus, message: str) -> None:
            self.respond_json(status, {"error": message})

        def respond(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


def main() -> int:
    config = load_config()
    controller = WolfController(config, Runner(config))
    handler = make_handler(controller, config)
    server = ThreadingHTTPServer((config.bind_host, config.bind_port), handler)
    print(
        f"wolf listening on {config.bind_host}:{config.bind_port}",
        flush=True,
    )
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
