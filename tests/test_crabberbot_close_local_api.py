"""Verify local Telegram close ordering and refusal while the source bot runs."""

import importlib.util
import io
import json
from pathlib import Path
import subprocess

import pytest


@pytest.fixture
def closing(tmp_path, monkeypatch):
    """Load the helper with inert source containers and API requests."""
    spec = importlib.util.spec_from_file_location(
        "crabberbot_close_local_api",
        Path(__file__).parents[1] / "proxmox/crabberbot-close-local-api.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(module.socket, "getfqdn", lambda: "docker.home.arpa")
    token = tmp_path / "token"
    token.write_text("fixture-private-token\n")
    monkeypatch.setattr(module, "TOKEN", token)
    containers = [
        {"State": {"Running": False}},
        {
            "State": {"Running": True},
            "NetworkSettings": {
                "Networks": {"crabberbot_backend": {"IPAddress": "172.20.0.2"}}
            },
        },
    ]
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 0, json.dumps(containers), ""
        ),
    )
    calls = []

    def urlopen(request, *, timeout):
        """Capture requests and return a successful API envelope."""
        calls.append((request, timeout))
        return io.BytesIO(b'{"ok":true,"result":true}')

    monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
    return module, containers, calls


def test_close_preserves_updates_and_uses_only_local_api(closing, capsys):
    """Delete the webhook without discarding updates before closing the session."""
    module, _, calls = closing
    module.close_session()
    assert [request.full_url.rsplit("/", 1)[1] for request, _ in calls] == [
        "deleteWebhook",
        "close",
    ]
    assert calls[0][0].data == b"drop_pending_updates=false"
    assert all(
        request.method == "POST" and request.host == "172.20.0.2:8081" and timeout == 10
        for request, timeout in calls
    )
    assert capsys.readouterr().out == ""


def test_running_source_bot_prevents_api_mutation(closing):
    """Never unregister a webhook while the source bot remains active."""
    module, containers, calls = closing
    containers[0]["State"]["Running"] = True
    with pytest.raises(RuntimeError, match="Stop the source bot"):
        module.close_session()
    assert not calls
