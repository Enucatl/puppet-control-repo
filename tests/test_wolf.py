import importlib.util
import json
import subprocess
import sys
import time
from collections.abc import Sequence
from email.message import Message
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[1] / "docker" / "wolf" / "app" / "wolf.py"
SPEC = importlib.util.spec_from_file_location("wolf", MODULE_PATH)
assert SPEC is not None
wolf = importlib.util.module_from_spec(SPEC)
sys.modules["wolf"] = wolf
assert SPEC.loader is not None
SPEC.loader.exec_module(wolf)

API_TOKEN = "api-token"


class FakeRunner:
    def __init__(self, responses: list[tuple[str, int, str]]) -> None:
        self.responses = responses
        self.commands: list[str] = []
        self.inputs: list[str | None] = []
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
        del timeout, capture_output
        command_text = " ".join(command)
        self.commands.append(command_text)
        self.inputs.append(input_text)
        self.envs.append(env)
        if not self.responses:
            raise AssertionError(f"unexpected command: {command_text}")
        expected, returncode, stdout = self.responses.pop(0)
        if expected not in command_text:
            raise AssertionError(f"expected {expected!r}, got {command_text!r}")
        result = subprocess.CompletedProcess(command, returncode, stdout, "error")
        if check and returncode != 0:
            raise wolf.CommandError(command, result)
        return result


class FakeHttp:
    def __init__(self, responses: list[tuple[str, int, str]]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, str, Sequence[tuple[str, str]] | None]] = []

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
        del timeout, verify
        self.requests.append((method, url, data))
        assert headers == {"Authorization": f"PVEAPIToken={API_TOKEN}"}
        if not self.responses:
            raise AssertionError(f"unexpected API request: {method} {url}")
        expected, returncode, stdout = self.responses.pop(0)
        request_text = f"{method} {url}"
        if data is not None:
            request_text = f"{request_text} {data}"
        if expected not in request_text:
            raise AssertionError(f"expected {expected!r}, got {request_text!r}")
        if returncode != 0:
            raise RuntimeError("API request failed")
        return subprocess.CompletedProcess([method, url], returncode, stdout, "")


def test_start_runs_fixed_wolf_sequence(tmp_path: Path) -> None:
    runner = FakeRunner(
        [
            ("nc -z -w 2 proxmox-cortex.home.arpa 8006", 1, ""),
            ("nc -z -w 2 proxmox-cortex.home.arpa 8006", 1, ""),
            ("vault kv get -field=proxmox-cortex kv/wolf", 0, "secret\n"),
            ("wakeonlan -i 10.0.0.255 30:56:0f:5e:a9:de", 0, ""),
            ("nc -z -w 1 dropbear.proxmox-cortex.home.arpa 2222", 0, ""),
            ("expect -", 0, ""),
            ("nc -z -w 1 proxmox-cortex.home.arpa 8006", 0, ""),
            ("nc -z -w 1 complex.home.arpa 22", 0, ""),
            ("nc -z -w 2 proxmox-cortex.home.arpa 8006", 0, ""),
            ("nc -z -w 1 complex.home.arpa 22", 0, ""),
        ]
    )
    http = FakeHttp(
        [
            (
                "GET https://proxmox-cortex.home.arpa:8006/api2/json/nodes/proxmox-cortex/qemu/200/status/current",
                0,
                '{"data": {"status": "stopped"}}',
            ),
            (
                "POST https://proxmox-cortex.home.arpa:8006/api2/json/nodes/proxmox-cortex/qemu/200/status/start",
                0,
                '{"data": {}}',
            ),
            (
                "POST https://proxmox-cortex.home.arpa:8006/api2/json/nodes/proxmox-cortex/qemu/200/agent/exec",
                0,
                '{"data": {"exitcode": 0, "out-data": ""}}',
            ),
            (
                "GET https://proxmox-cortex.home.arpa:8006/api2/json/nodes/proxmox-cortex/qemu/200/status/current",
                0,
                '{"data": {"status": "running"}}',
            ),
            (
                "POST https://proxmox-cortex.home.arpa:8006/api2/json/nodes/proxmox-cortex/qemu/200/agent/exec",
                0,
                '{"data": {"exitcode": 0, "out-data": "wolf\\n"}}',
            ),
        ]
    )
    config = wolf.Config(
        lock_file=str(tmp_path / "lock"),
        state_file=str(tmp_path / "state.json"),
        max_session_seconds=3600,
    )
    controller = wolf.WolfController(config, runner, http)
    controller._proxmox_api_token = API_TOKEN

    status = controller.start_session("friend")

    assert status["active"] is True
    assert status["phase"] == "running"
    assert status["ownership"] == "app"
    assert status["observed"]["wolf"] == "running"
    assert status["started_by"] == "friend"
    assert any(
        data is not None
        and ("command", "cd /opt/docker/wolf && docker compose up -d") in data
        for _, _, data in http.requests
    )
    assert all(API_TOKEN not in command for command in runner.commands)
    assert runner.inputs[5] is not None
    assert "log_user 0" in runner.inputs[5]
    assert (
        "ssh -i /run/secrets/wolf_dropbear_key -p 2222 -o UserKnownHostsFile=/run/secrets/wolf_dropbear_known_hosts -o StrictHostKeyChecking=yes root@dropbear.proxmox-cortex.home.arpa"
        in runner.inputs[5]
    )


def test_stop_uses_fixed_shutdown_path(tmp_path: Path) -> None:
    runner = FakeRunner(
        [
            ("nc -z -w 2 proxmox-cortex.home.arpa 8006", 0, ""),
            ("nc -z -w 1 complex.home.arpa 22", 0, ""),
            ("nc -z -w 2 proxmox-cortex.home.arpa 8006", 1, ""),
        ]
    )
    http = FakeHttp(
        [
            (
                "GET https://proxmox-cortex.home.arpa:8006/api2/json/nodes/proxmox-cortex/qemu/200/status/current",
                0,
                '{"data": {"status": "running"}}',
            ),
            (
                "POST https://proxmox-cortex.home.arpa:8006/api2/json/nodes/proxmox-cortex/qemu/200/agent/exec",
                0,
                '{"data": {"exitcode": 0, "out-data": "wolf\\n"}}',
            ),
            (
                "POST https://proxmox-cortex.home.arpa:8006/api2/json/nodes/proxmox-cortex/qemu/200/agent/exec",
                0,
                '{"data": {"exitcode": 0, "out-data": ""}}',
            ),
            (
                "POST https://proxmox-cortex.home.arpa:8006/api2/json/nodes/proxmox-cortex/qemu/200/status/shutdown",
                0,
                '{"data": {}}',
            ),
            (
                "POST https://proxmox-cortex.home.arpa:8006/api2/json/nodes/proxmox-cortex/status/shutdown",
                0,
                '{"data": {}}',
            ),
        ]
    )
    config = wolf.Config(
        lock_file=str(tmp_path / "lock"), state_file=str(tmp_path / "state.json")
    )
    controller = wolf.WolfController(config, runner, http)
    controller._proxmox_api_token = API_TOKEN
    controller.state.phase = "running"
    controller.state.started_at = 1

    status = controller.stop_session("manual")

    assert status["active"] is False
    assert status["phase"] == "idle"
    assert status["last_stop_reason"] == "manual"


def test_failed_start_transitions_to_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FakeRunner(
        [
            ("nc -z -w 2 proxmox-cortex.home.arpa 8006", 0, ""),
            ("nc -z -w 1 complex.home.arpa 22", 0, ""),
            ("nc -z -w 2 proxmox-cortex.home.arpa 8006", 0, ""),
        ]
    )
    http = FakeHttp(
        [
            (
                "GET https://proxmox-cortex.home.arpa:8006/api2/json/nodes/proxmox-cortex/qemu/200/status/current",
                0,
                '{"data": {"status": "running"}}',
            ),
            (
                "POST https://proxmox-cortex.home.arpa:8006/api2/json/nodes/proxmox-cortex/qemu/200/agent/exec",
                0,
                '{"data": {"exitcode": 0, "out-data": ""}}',
            ),
            (
                "GET https://proxmox-cortex.home.arpa:8006/api2/json/nodes/proxmox-cortex/qemu/200/status/current",
                0,
                '{"data": {"status": "running"}}',
            ),
        ]
    )
    config = wolf.Config(
        lock_file=str(tmp_path / "lock"), state_file=str(tmp_path / "state.json")
    )
    controller = wolf.WolfController(config, runner, http)
    controller._proxmox_api_token = API_TOKEN

    def fail_wait_for_port(*args: object, **kwargs: object) -> None:
        raise RuntimeError("timed out waiting for Wolf guest SSH")

    monkeypatch.setattr(controller, "wait_for_port", fail_wait_for_port)

    with pytest.raises(RuntimeError, match="timed out"):
        controller.start_session("friend")

    assert controller.state.phase == "failed"


def test_status_reports_external_wolf_without_claiming_ownership(
    tmp_path: Path,
) -> None:
    runner = FakeRunner(
        [
            ("nc -z -w 2 proxmox-cortex.home.arpa 8006", 0, ""),
            ("nc -z -w 1 complex.home.arpa 22", 0, ""),
        ]
    )
    http = FakeHttp(
        [
            (
                "GET https://proxmox-cortex.home.arpa:8006/api2/json/nodes/proxmox-cortex/qemu/200/status/current",
                0,
                '{"data": {"status": "running"}}',
            ),
            (
                "POST https://proxmox-cortex.home.arpa:8006/api2/json/nodes/proxmox-cortex/qemu/200/agent/exec",
                0,
                '{"data": {"exitcode": 0, "out-data": "wolf\\n"}}',
            ),
        ]
    )
    config = wolf.Config(
        lock_file=str(tmp_path / "lock"), state_file=str(tmp_path / "state.json")
    )
    controller = wolf.WolfController(config, runner, http)
    controller._proxmox_api_token = API_TOKEN

    status = controller.status()

    assert status["active"] is True
    assert status["phase"] == "idle"
    assert status["ownership"] == "external"
    assert status["observed"]["wolf"] == "running"


def test_status_reconciles_lost_app_session(tmp_path: Path) -> None:
    runner = FakeRunner(
        [
            ("nc -z -w 2 proxmox-cortex.home.arpa 8006", 0, ""),
            ("nc -z -w 1 complex.home.arpa 22", 0, ""),
        ]
    )
    http = FakeHttp(
        [
            (
                "GET https://proxmox-cortex.home.arpa:8006/api2/json/nodes/proxmox-cortex/qemu/200/status/current",
                0,
                '{"data": {"status": "running"}}',
            ),
            (
                "POST https://proxmox-cortex.home.arpa:8006/api2/json/nodes/proxmox-cortex/qemu/200/agent/exec",
                0,
                '{"data": {"exitcode": 0, "out-data": ""}}',
            ),
        ]
    )
    config = wolf.Config(
        lock_file=str(tmp_path / "lock"), state_file=str(tmp_path / "state.json")
    )
    controller = wolf.WolfController(config, runner, http)
    controller._proxmox_api_token = API_TOKEN
    controller.state.phase = "running"
    controller.state.started_at = 1
    controller.state.deadline = time.time() + 60

    status = controller.status()

    assert status["active"] is False
    assert status["phase"] == "failed"
    assert status["last_result"] == "lost"
    assert status["last_stop_reason"] == "external"


def test_authorization_requires_remote_user_and_group() -> None:
    config = wolf.Config()
    controller = wolf.WolfController(config, wolf.Runner(config))
    handler = wolf.make_handler(controller, config)

    assert handler is not None


def test_dropbear_key_uses_secret_mount() -> None:
    assert wolf.Config().dropbear_key == "/run/secrets/wolf_dropbear_key"


def test_running_state_is_loaded_from_state_file(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    state_file.write_text(
        json.dumps(
            {
                "phase": "running",
                "started_by": "friend",
                "started_at": time.time(),
                "deadline": time.time() + 3600,
                "last_action": "start",
                "last_result": "ok",
                "last_stop_reason": None,
            }
        ),
        encoding="utf-8",
    )
    config = wolf.Config(lock_file=str(tmp_path / "lock"), state_file=str(state_file))
    controller = wolf.WolfController(config, FakeRunner([]), FakeHttp([]))

    assert controller.state.phase == "running"
    assert controller.state.started_by == "friend"
    assert controller.timeout_timer is not None
    controller.cancel_timeout()


def test_origin_and_csrf_checks(tmp_path: Path) -> None:
    config = wolf.Config(
        lock_file=str(tmp_path / "lock"), state_file=str(tmp_path / "state.json")
    )
    controller = wolf.WolfController(config, FakeRunner([]), FakeHttp([]))
    handler_class = wolf.make_handler(controller, config)
    handler = object.__new__(handler_class)
    headers = Message()
    headers["Host"] = "wolf.home.arpa"
    headers["Origin"] = "https://wolf.home.arpa"
    headers["X-Wolf-CSRF-Token"] = controller.csrf_token
    handler.headers = headers

    assert handler.origin_allowed() is True
    assert handler.csrf_token_valid({}) is True

    handler.headers.replace_header("Origin", "https://evil.example")

    assert handler.origin_allowed() is False


def test_healthz_is_public(tmp_path: Path) -> None:
    config = wolf.Config(
        lock_file=str(tmp_path / "lock"), state_file=str(tmp_path / "state.json")
    )
    controller = wolf.WolfController(config, FakeRunner([]), FakeHttp([]))
    handler_class = wolf.make_handler(controller, config)
    handler = object.__new__(handler_class)
    captured: dict[str, object] = {}

    def respond(status: object, body: bytes, content_type: str) -> None:
        captured["status"] = status
        captured["body"] = body
        captured["content_type"] = content_type

    handler.respond = respond  # type: ignore[method-assign]

    handler.handle_healthz()

    assert captured["status"] == wolf.HTTPStatus.OK
    assert captured["body"] == b"ok\n"
    assert captured["content_type"] == "text/plain; charset=utf-8"


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({}, False),
        ({"Remote-User": "friend", "Remote-Groups": "admins"}, False),
        ({"Remote-User": "friend", "Remote-Groups": "admins,wolf-operators"}, True),
    ],
)
def test_header_authorization_logic(headers: dict[str, str], expected: bool) -> None:
    groups = {
        group.strip()
        for group in headers.get("Remote-Groups", "").split(",")
        if group.strip()
    }

    authorized = bool(headers.get("Remote-User")) and "wolf-operators" in groups

    assert authorized is expected
