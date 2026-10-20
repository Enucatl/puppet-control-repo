"""Check mail handover boundaries without accessing credentials or services."""

import importlib.util
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def handover(monkeypatch):
    """Load the standalone command with the expected host identity."""
    spec = importlib.util.spec_from_file_location(
        "atlas_bridge_mail_handover",
        Path(__file__).parents[1] / "proxmox/atlas-bridge-mail-handover.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(module.socket, "getfqdn", lambda: "atlas.home.arpa")
    return module


def test_imap_hostname_changes_only_after_successful_authentication(
    handover, monkeypatch
):
    """Keep the source endpoint if authentication fails and preserve other fields."""
    account = SimpleNamespace(
        pk=1, imap_server="docker.home.arpa", imap_port=10243, imap_security=2
    )
    objects = Mock()
    objects.get.return_value = account
    model = SimpleNamespace(objects=objects, ImapSecurity=SimpleNamespace(SSL=2))
    mailbox = Mock()
    login = Mock(side_effect=RuntimeError("private authentication diagnostic"))
    monkeypatch.setitem(
        sys.modules, "paperless_mail.models", SimpleNamespace(MailAccount=model)
    )
    monkeypatch.setitem(
        sys.modules,
        "paperless_mail.mail",
        SimpleNamespace(get_mailbox=Mock(return_value=mailbox), mailbox_login=login),
    )
    with pytest.raises(RuntimeError):
        exec(handover.PAPERLESS, {})
    objects.filter.assert_not_called()
    mailbox.logout.assert_called_once()
    login.side_effect = None
    exec(handover.PAPERLESS, {})
    objects.filter.assert_called_once_with(pk=1)
    objects.filter.return_value.update.assert_called_once_with(
        imap_server="atlas.home.arpa"
    )


def test_authentication_errors_are_private_and_stop_handover(handover, monkeypatch):
    """Never expose subprocess output or update Paperless after SMTP failure."""
    run = Mock(return_value=subprocess.CompletedProcess([], 1, "private", "secret"))
    monkeypatch.setattr(handover.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="diagnostics withheld") as error:
        handover.handover()
    assert "private" not in str(error.value) and "secret" not in str(error.value)
    run.assert_called_once()
    assert run.call_args.args[0][3] == "send-bills-bills-1"
    assert run.call_args.kwargs["capture_output"]


def test_other_hosts_are_rejected_before_commands(handover, monkeypatch):
    """Prevent an accidental source-host execution before any client operation."""
    monkeypatch.setattr(handover.socket, "getfqdn", lambda: "docker.home.arpa")
    run = Mock()
    monkeypatch.setattr(handover.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="root on Atlas"):
        handover.handover()
    run.assert_not_called()
