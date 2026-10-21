"""Check CA-less FreeIPA authority reuse without Docker or secret disclosure."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import shlex
from types import ModuleType
from unittest.mock import Mock

import pytest


@pytest.fixture
def controller(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Load the controller with disposable state and inert external operations."""
    spec = importlib.util.spec_from_file_location(
        "atlas_freeipa",
        Path(__file__).resolve().parents[1] / "proxmox/atlas-freeipa.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "STATE", tmp_path)
    monkeypatch.setattr(
        module.infra, "IPA_SECRETS", tmp_path / "secrets", raising=False
    )
    monkeypatch.setattr(module.infra, "CA", tmp_path / "ca.pem", raising=False)
    module.infra.CA.write_text("root")
    monkeypatch.setattr(module.os, "chown", Mock())
    for name in ("api", "run", "mount"):
        monkeypatch.setattr(module.infra, name, Mock(name=name))
    monkeypatch.setattr(module, "compose", Mock())
    module.compose.return_value = json.dumps(
        {"services": {"ipa": {"image": "native-freeipa-image"}}}
    )
    return module


@pytest.mark.parametrize(
    "checkpoint", ["intermediate.json", "intermediate.pem", "issuer.json"]
)
def test_missing_established_mount_is_never_recreated(
    controller: ModuleType, checkpoint: str
) -> None:
    """Loss of persisted authority storage requires recovery."""
    (controller.STATE / checkpoint).write_text("{}")
    controller.infra.api.return_value = {"data": {}}
    with pytest.raises(RuntimeError, match="mount missing"):
        controller.intermediate()
    controller.infra.mount.assert_not_called()
    assert all(call.args[0] == "GET" for call in controller.infra.api.call_args_list)


def test_missing_original_key_cannot_generate_another(controller: ModuleType) -> None:
    """An interrupted issuance retains its key identity."""
    (controller.STATE / "intermediate.json").write_text(
        json.dumps({"key_id": "original", "csr": "CSR"})
    )
    controller.infra.api.side_effect = [
        {"data": {"pki_freeipa/": {}}},
        {"data": {"keys": ["replacement"]}},
        {"data": {"keys": []}},
    ]
    with pytest.raises(RuntimeError, match="Original FreeIPA intermediate key missing"):
        controller.intermediate()
    assert not any(
        call.args[0] == "POST" for call in controller.infra.api.call_args_list
    )


def test_intermediate_reuses_saved_certificate_and_key(
    controller: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Established issuance performs no signing, export or key generation."""
    identity = {"key_id": "key", "issuer_id": "issuer"}
    (controller.STATE / "intermediate.json").write_text(
        json.dumps({"key_id": "key", "csr": "CSR"})
    )
    (controller.STATE / "intermediate.pem").write_text(
        "certificate-----END CERTIFICATE-----\n"
    )
    (controller.STATE / "issuer.json").write_text(json.dumps(identity))
    monkeypatch.setattr(
        controller.ssl, "PEM_cert_to_DER_cert", lambda value: b"certificate"
    )
    controller.infra.api.side_effect = [
        {"data": {"pki_freeipa/": {}}},
        {"data": {"keys": ["key"]}},
        {"data": {"keys": ["issuer"]}},
        {"data": {**identity, "certificate": "certificate"}},
    ]
    controller.intermediate()
    assert [call.args[0] for call in controller.infra.api.call_args_list] == [
        "GET",
        "LIST",
        "LIST",
        "GET",
    ]


def test_issuance_is_exact_name_and_never_exports_root(
    controller: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One protected pair bootstraps HTTP and LDAP without duplicating key JSON."""
    monkeypatch.setattr(controller.ssl, "PEM_cert_to_DER_cert", lambda value: value)
    monkeypatch.setattr(controller, "intermediate", Mock())
    password = "spaces and '$()' preserve this"
    monkeypatch.setattr(controller.secrets, "token_urlsafe", lambda _: password)
    leaf = {
        "certificate": "leaf",
        "ca_chain": ["issuer", "root"],
        "private_key": "seed-only-pkcs8",
    }
    controller.infra.api.side_effect = [
        {"data": {"certificate": "root"}},
        {},
        {"data": leaf},
    ]
    controller.initialize()
    calls = controller.infra.api.call_args_list
    assert [(call.args[0], call.args[1]) for call in calls] == [
        ("GET", "pki/cert/ca"),
        ("POST", "pki_freeipa/roles/ipa-transport"),
        ("POST", "pki_freeipa/issue/ipa-transport"),
    ]
    role = calls[1].args[2]
    assert role["allowed_domains"] == ["ipa.home.arpa"]
    assert role["allow_subdomains"] is False
    assert role["allow_ip_sans"] is False
    assert role["allow_wildcard_certificates"] is False
    assert role["key_usage"] == ["DigitalSignature"]
    assert role["max_ttl"] == "2160h"
    secrets = controller.infra.IPA_SECRETS
    assert (secrets / "fullchain.pem").read_text() == "leaf\nissuer\nroot\n"
    assert (secrets / "key.pem").read_text() == leaf["private_key"]
    assert (secrets / "ca.pem").read_text() == "root"
    settings = json.loads((secrets / "installer.json").read_text())
    assert settings["no_pkinit"] is True
    assert "volume_identity" not in settings
    options = secrets / "ipa-server-install-options"
    parsed = shlex.split(options.read_text())
    assert parsed == [f"--admin-password={password}", f"--ds-password={password}"]
    assert stat.S_IMODE(secrets.stat().st_mode) == 0o700
    for path in secrets.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        controller.os.chown.assert_any_call(path, 100000, 100000)
    controller.os.chown.assert_any_call(secrets, 100000, 100000)
    assert not (controller.STATE / "ready.json").exists()
    assert not (controller.STATE / "http.json").exists()
    assert not (controller.STATE / "ldap.json").exists()
    assert password not in repr(controller.infra.run.call_args_list)
    command = controller.infra.run.call_args.args[0]
    assert "--read-only" in command
    assert "type=volume,src=infra_ipa_data,dst=/data,readonly" in command
    assert command[-1] == 'test -z "$(ls -A /data)"'


def test_populated_native_volume_refuses_bootstrap(controller: ModuleType) -> None:
    """Reject an existing server before issuing or replacing any credentials."""
    data = controller.STATE / "data"
    data.mkdir()
    existing = data / "installed-state"
    existing.write_text("preserved")

    def run(arguments: list[str]) -> str:
        """Execute the real bootstrap preflight against disposable storage."""
        if "create" in arguments:
            return controller.VOLUME
        subprocess.run(
            ["sh", "-ec", arguments[-1].replace("/data", shlex.quote(str(data)))],
            check=True,
        )
        return ""

    controller.infra.run.side_effect = run
    with pytest.raises(subprocess.CalledProcessError):
        controller.initialize()
    controller.infra.api.assert_not_called()
    assert not controller.infra.IPA_SECRETS.exists()
    assert existing.read_text() == "preserved"
