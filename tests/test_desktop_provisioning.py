"""Exercise desktop provisioning without Vault access or real virtual machines."""

from __future__ import annotations

import importlib
import json
import subprocess
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest


PROXMOX = Path(__file__).resolve().parents[1] / "proxmox"
CERTNAME = "desktop.home.arpa"
AUTH = {
    "client_token": "dummy-enrollment-token",
    "policies": ["puppet-enrollment"],
    "metadata": {"certname": CERTNAME},
    "token_type": "service",
    "orphan": True,
    "renewable": False,
    "num_uses": 1,
    "lease_duration": 7200,
}


@pytest.fixture
def desktop(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Import the executable's sibling modules using its normal import path."""
    monkeypatch.syspath_prepend(str(PROXMOX))
    return importlib.import_module("desktop")


def test_cli_preserves_defaults_and_short_options(desktop: ModuleType) -> None:
    """Keep existing desktop.sh invocations usable after migration."""
    defaults = desktop.parse_args([])
    assert (defaults.letter, defaults.vmid) == ("", None)
    assert (defaults.cores, defaults.memory, defaults.disk_size) == (8, 8000, "128G")
    explicit = desktop.parse_args(
        ["-l", "d", "-i", "123", "-c", "4", "-m", "4096", "-d", "64G"]
    )
    assert (explicit.letter, explicit.vmid) == ("d", "123")
    assert (explicit.cores, explicit.memory, explicit.disk_size) == (4, 4096, "64G")


def test_settings_preserve_quoted_literals(
    desktop: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Read values with spaces and shell syntax without executing or expanding them."""
    monkeypatch.setattr(desktop, "SCRIPT_DIR", tmp_path)
    (tmp_path / "config.sh").write_text(
        'DOMAIN_SUFFIX="home.arpa"\nPUPPET_SERVER="docker.${DOMAIN_SUFFIX}"\n'
        'DEFAULT_STORAGE="local-zfs"\nSNIPPET_STORAGE="local"\nTEMPLATE_ID=9000\n'
    )
    marker = tmp_path / "must-not-exist"
    token = f"literal $dollar $(touch {marker})"
    env = tmp_path / ".env"
    env.write_text(
        f"VAULT_TOKEN='{token}'\n"
        'VAULT_ADDR="https://vault.example:8200"\n'
        f'LOG_DIR="{tmp_path / "logs with spaces"}"\n'
        'VAULT_CACERT="/tmp/custom ca.pem" # comment\n'
    )
    settings = desktop.load_settings(env)
    assert settings["VAULT_TOKEN"] == token
    assert settings["PUPPET_SERVER"] == "docker.home.arpa"
    assert settings["LOG_DIR"] == str(tmp_path / "logs with spaces")
    assert settings["VAULT_CACERT"] == "/tmp/custom ca.pem"
    assert not marker.exists()


@pytest.mark.parametrize(
    "args", [["-l", "a.*"], ["-i", "0"], ["-c", "0"], ["-m", "-1"], ["-d", "--help"]]
)
def test_cli_rejects_invalid_parameters(desktop: ModuleType, args: list[str]) -> None:
    """Reject invalid arguments before they can reach the VM commands."""
    with pytest.raises(SystemExit) as error:
        desktop.parse_args(args)
    assert error.value.code == 2


@pytest.mark.parametrize("vmid", ["9000", "not-an-id"])
def test_auto_vmid_cannot_overwrite_template(
    desktop: ModuleType, monkeypatch: pytest.MonkeyPatch, vmid: str
) -> None:
    """Validate IDs from Proxmox as well as IDs supplied through the CLI."""
    run = Mock(return_value=subprocess.CompletedProcess([], 0, vmid + "\n", ""))
    monkeypatch.setattr(desktop, "run", run)
    with pytest.raises(ValueError, match="VMID"):
        desktop.provision(desktop.parse_args([]), {"TEMPLATE_ID": "9000"})
    run.assert_called_once_with("pvesh", "get", "/cluster/nextid")


@pytest.mark.parametrize("ca", [None, "/tmp/custom ca.pem"])
def test_vault_request_is_restricted_and_verified(
    desktop: ModuleType, monkeypatch: pytest.MonkeyPatch, ca: str | None
) -> None:
    """Issue an orphan token without leaking inherited policies or disabling TLS."""
    post = Mock(
        return_value=SimpleNamespace(
            raise_for_status=Mock(), json=lambda: {"auth": AUTH}, status_code=200
        )
    )
    monkeypatch.setattr(desktop.niquests, "post", post)
    settings = {"VAULT_ADDR": "https://vault.example:8200", "VAULT_TOKEN": "issuer"}
    if ca:
        settings["VAULT_CACERT"] = ca
    assert desktop.create_vault_token(settings, CERTNAME) == AUTH["client_token"]
    post.assert_called_once_with(
        "https://vault.example:8200/v1/auth/token/create-orphan",
        headers={"X-Vault-Token": "issuer"},
        json={
            "policies": ["puppet-enrollment"],
            "type": "service",
            "ttl": "2h",
            "num_uses": 1,
            "renewable": False,
            "no_default_policy": True,
            "meta": {"certname": CERTNAME},
        },
        verify=ca or True,
        timeout=30,
        allow_redirects=False,
    )


@pytest.mark.parametrize(
    "changed",
    [
        {"client_token": ""},
        {"client_token": None},
        {"policies": ["default", "puppet-enrollment"]},
        {"metadata": {"certname": "other.home.arpa"}},
        {"metadata": None},
        {"token_type": "batch"},
        {"orphan": False},
        {"renewable": True},
        {"num_uses": 0},
        {"num_uses": True},
        {"lease_duration": 0},
        {"lease_duration": 7201},
        {"lease_duration": "7200"},
        {"lease_duration": True},
    ],
)
def test_vault_rejects_unrestricted_or_malformed_tokens(
    desktop: ModuleType, monkeypatch: pytest.MonkeyPatch, changed: dict[str, object]
) -> None:
    """Fail closed when a response changes any enrollment restriction."""
    monkeypatch.setattr(
        desktop.niquests,
        "post",
        Mock(
            return_value=SimpleNamespace(
                raise_for_status=Mock(),
                json=lambda: {"auth": {**AUTH, **changed}},
                status_code=200,
            )
        ),
    )
    with pytest.raises(ValueError, match="Invalid Vault enrollment"):
        desktop.create_vault_token(
            {"VAULT_ADDR": "https://vault.example", "VAULT_TOKEN": "issuer"},
            CERTNAME,
        )


def test_command_arguments_do_not_pass_through_a_shell(desktop: ModuleType) -> None:
    """Subprocess arguments retain spaces, dollars, and quotes verbatim."""
    value = 'literal $HOME "quote" ; exit 9'
    result = desktop.run("printf", "%s", value)
    assert result.stdout == value


@pytest.mark.parametrize("failed", [False, True])
def test_cloudinit_readiness_and_diagnostics(
    desktop: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failed: bool,
) -> None:
    """Retry an unavailable guest agent and surface cloud-init failure details."""
    commands: list[tuple[str, ...]] = []
    sleeps: list[int] = []

    def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        """Return an agent failure, running status, and then a terminal status."""
        commands.append(args)
        if len(commands) == 1:
            if check:
                raise subprocess.CalledProcessError(1, args)
            return subprocess.CompletedProcess(args, 1, "", "not ready")
        if args[-2:] == ("cloud-init", "status"):
            state = "running" if len(commands) == 2 else "error" if failed else "done"
            output = f"status: {state}"
        else:
            output = "guest failure details"
        return subprocess.CompletedProcess(
            args, 0, json.dumps({"out-data": output}), ""
        )

    monkeypatch.setattr(desktop, "run", run)
    monkeypatch.setattr(desktop.time, "sleep", sleeps.append)
    if failed:
        with pytest.raises(RuntimeError):
            desktop.wait_for_cloudinit("123")
        assert "guest failure details" in capsys.readouterr().err
        assert any(
            command[-3:] == ("cloud-init", "status", "--long") for command in commands
        )
        assert any("journalctl" in " ".join(command) for command in commands)
    else:
        desktop.wait_for_cloudinit("123")
    assert len(sleeps) == 2


@pytest.mark.parametrize("failure", [None, "wait", "reboot", "token"])
def test_provision_lifecycle_and_cleanup(
    desktop: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str | None,
) -> None:
    """Clean failed clones while preserving configured VMs after reboot failures."""
    commands: list[tuple[str, ...]] = []
    snippets = tmp_path / "snippets"
    snippets.mkdir()
    logs = tmp_path / "logs"
    logs.mkdir()
    settings = {
        "DOMAIN_SUFFIX": "home.arpa",
        "PUPPET_SERVER": "puppet.home.arpa",
        "VAULT_ADDR": "https://vault.example",
        "VAULT_TOKEN": "issuer",
        "DEFAULT_STORAGE": "local-zfs",
        "SNIPPET_STORAGE": "local",
        "TEMPLATE_ID": "9000",
        "LOG_DIR": str(logs),
    }

    def issue(_settings: dict[str, str], certname: str) -> str:
        """Require token validation before any destructive Proxmox command."""
        assert certname == CERTNAME
        assert not any(command[0] == "qm" for command in commands)
        if failure == "token":
            raise RuntimeError("token failed")
        return str(AUTH["client_token"])

    def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        """Record Proxmox commands without modifying a host."""
        commands.append(args)
        if args[:2] == ("qm", "reboot") and failure == "reboot":
            raise RuntimeError("reboot failed")
        if args[:2] == ("qm", "clone"):
            snippet = snippets / "generated-desktop-123.yml"
            assert str(AUTH["client_token"]) in snippet.read_text()
            assert snippet.stat().st_mode & 0o077 == 0
        return subprocess.CompletedProcess(
            args,
            0,
            json.dumps({"exitcode": 0, "out-data": "provisioned guest log"}),
            "",
        )

    def wait(vmid: str) -> None:
        """Simulate the guest's terminal provisioning state."""
        assert vmid == "123"
        if failure == "wait":
            raise RuntimeError("wait failed")

    monkeypatch.setattr(desktop, "SNIPPET_DIR", snippets)
    monkeypatch.setattr(desktop, "pick_vm_name", lambda letter: "desktop")
    monkeypatch.setattr(desktop, "create_vault_token", issue)
    monkeypatch.setattr(desktop, "run", run)
    monkeypatch.setattr(desktop, "wait_for_cloudinit", wait)
    args = desktop.parse_args(["-i", "123"])
    if failure:
        with pytest.raises(RuntimeError, match=f"{failure} failed"):
            desktop.provision(args, settings)
    else:
        assert desktop.provision(args, settings) == "desktop"
    assert list(snippets.iterdir()) == []
    if failure == "token":
        assert commands == []
        return
    clone = commands.index(
        (
            "qm",
            "clone",
            "9000",
            "123",
            "--name",
            "desktop",
            "--storage",
            "local-zfs",
            "--full",
            "1",
        )
    )
    assert ("qm", "destroy", "123", "--purge") in commands[:clone]
    assert ("qm", "resize", "123", "scsi0", "128G") in commands
    assert (
        "qm",
        "set",
        "123",
        "--agent",
        "1",
        "--cores",
        "8",
        "--memory",
        "8000",
        "--cpu",
        "host",
    ) in commands
    assert ("qm", "set", "123", "--machine", "q35", "--bios", "ovmf") in commands
    assert (
        "qm",
        "set",
        "123",
        "--efidisk0",
        "local-zfs:0,efitype=4m,pre-enrolled-keys=1",
    ) in commands
    if failure == "wait":
        assert commands[-2:] == [
            ("qm", "stop", "123"),
            ("qm", "destroy", "123", "--purge"),
        ]
    else:
        assert not any(
            command[:2] == ("qm", "destroy") for command in commands[clone + 1 :]
        )
        assert commands[-1] == ("qm", "reboot", "123")
        assert "provisioned guest log" in (logs / "cloud-init-output.log").read_text()
