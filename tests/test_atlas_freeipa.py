"""Check CA-less FreeIPA authority reuse without Docker or secret disclosure."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
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
    for name in ("api", "run", "mount"):
        monkeypatch.setattr(module.infra, name, Mock(name=name))
    monkeypatch.setattr(module, "compose", Mock())
    return module


def test_start_cannot_initialize(controller: ModuleType) -> None:
    """A missing checkpoint fails before any external mutation."""
    with pytest.raises(RuntimeError, match="Missing initialized"):
        controller.start()
    controller.infra.api.assert_not_called()
    controller.infra.run.assert_not_called()
    controller.compose.assert_not_called()


def test_established_start_needs_no_vault_session(
    controller: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Vault outage cannot prevent starting intact local directory state."""
    monkeypatch.setattr(controller.infra, "STATE", controller.STATE)
    (controller.STATE / "ready.json").write_text(
        '{"hostname":"ipa.home.arpa","root_sha256":"pin"}'
    )
    (controller.STATE / "root.sha256").write_text("pin")
    for name in (
        "installer.json",
        "issuer.json",
        "intermediate.json",
        "intermediate.pem",
        "root.pem",
        "http.json",
        "ldap.json",
        "http-chain.pem",
        "ldap-chain.pem",
        "http-key.pem",
        "ldap-key.pem",
    ):
        (controller.STATE / name).write_text("saved")
    staged = controller.STATE / "container-inputs"
    staged.mkdir()
    for name in controller.INPUTS:
        (staged / name).write_text("saved")
    monkeypatch.setattr(controller, "volume", Mock())
    controller.infra.api.side_effect = RuntimeError("Vault unavailable")
    controller.start()
    controller.infra.api.assert_not_called()
    assert (controller.STATE / "installed").exists()


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


def test_replaced_volume_is_refused(controller: ModuleType) -> None:
    """Docker volume names alone cannot authorize empty replacement storage."""
    (controller.STATE / "volume.json").write_text(json.dumps({"identity": "identity"}))
    controller.infra.run.side_effect = [
        "infra_ipa_data\n",
        json.dumps(
            [{"Labels": {"home.arpa.authority": "atlas"}, "CreatedAt": "replacement"}]
        ),
        "different-identity\n",
    ]
    with pytest.raises(RuntimeError, match="volume identity mismatch"):
        controller.volume()
    assert controller.infra.run.call_count == 3


def test_volume_marker_cannot_be_recreated_on_start(controller: ModuleType) -> None:
    """A missing allocation never causes implicit volume creation."""
    controller.infra.run.return_value = ""
    with pytest.raises(RuntimeError, match="volume missing"):
        controller.volume()
    assert controller.infra.run.call_count == 1


def test_restored_volume_keeps_its_identity(controller: ModuleType) -> None:
    """Restored authority data may reside in a newly allocated Docker volume."""
    (controller.STATE / "volume.json").write_text('{"identity":"original"}')
    controller.infra.run.side_effect = [
        "infra_ipa_data\n",
        json.dumps([{"Labels": {"home.arpa.authority": "atlas"}, "CreatedAt": "new"}]),
        "original\n",
    ]
    controller.volume()
    assert controller.infra.run.call_count == 3


def test_start_refuses_changed_staged_key(
    controller: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The remapped container cannot silently use inputs unlike recovery state."""
    monkeypatch.setattr(controller.infra, "STATE", controller.STATE)
    (controller.STATE / "ready.json").write_text(
        '{"hostname":"ipa.home.arpa","root_sha256":"pin"}'
    )
    (controller.STATE / "root.sha256").write_text("pin")
    for name in (
        "installer.json",
        "issuer.json",
        "intermediate.json",
        "intermediate.pem",
        "root.pem",
        "http.json",
        "ldap.json",
        "http-chain.pem",
        "ldap-chain.pem",
        "http-key.pem",
        "ldap-key.pem",
    ):
        (controller.STATE / name).write_text("saved")
    staged = controller.STATE / "container-inputs"
    staged.mkdir()
    for name in controller.INPUTS:
        (staged / name).write_text("changed" if name == "http-key.pem" else "saved")
    with pytest.raises(RuntimeError, match="changed staged IPA input: http-key.pem"):
        controller.start()
    controller.compose.assert_not_called()
    controller.infra.api.assert_not_called()


def test_issuance_is_exact_name_and_never_exports_root(
    controller: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Actual role creation restricts identities and preserves issued seed keys."""
    existing = controller.STATE / "infra"
    existing.mkdir()
    (existing / "ready.json").write_text("{}")
    (existing / "root.sha256").write_text(
        controller.hashlib.sha256(b"root").hexdigest()
    )
    monkeypatch.setattr(controller.infra, "STATE", existing)
    monkeypatch.setattr(controller.ssl, "PEM_cert_to_DER_cert", lambda _: b"root")
    monkeypatch.setattr(controller.os, "chown", Mock())
    monkeypatch.setattr(controller, "intermediate", Mock())

    def prepare_volume(create: bool = False) -> None:
        """Provide an allocation identity without Docker."""
        assert create
        (controller.STATE / "volume.json").write_text('{"identity":"volume"}')

    monkeypatch.setattr(controller, "volume", prepare_volume)
    leaf = {
        "certificate": "leaf",
        "ca_chain": ["issuer", "root"],
        "private_key": "seed-only-pkcs8",
    }
    controller.infra.api.side_effect = [
        {"data": {"certificate": "root"}},
        {},
        {"data": leaf},
        {"data": leaf},
    ]
    controller.initialize()
    calls = controller.infra.api.call_args_list
    assert [(call.args[0], call.args[1]) for call in calls] == [
        ("GET", "pki/cert/ca"),
        ("POST", "pki_freeipa/roles/ipa-transport"),
        ("POST", "pki_freeipa/issue/ipa-transport"),
        ("POST", "pki_freeipa/issue/ipa-transport"),
    ]
    role = calls[1].args[2]
    assert role["allowed_domains"] == ["ipa.home.arpa"]
    assert role["allow_subdomains"] is False
    assert role["allow_ip_sans"] is False
    assert role["allow_wildcard_certificates"] is False
    assert role["key_usage"] == ["DigitalSignature"]
    assert role["max_ttl"] == "2160h"
    for service in ("http", "ldap"):
        assert (controller.STATE / f"{service}-key.pem").read_text() == leaf[
            "private_key"
        ]
        assert (
            controller.STATE / "container-inputs" / f"{service}-key.pem"
        ).read_text() == leaf["private_key"]
    settings = json.loads((controller.STATE / "installer.json").read_text())
    assert settings["no_pkinit"] is True
    assert settings["volume_identity"] == "volume"
    assert len(settings["admin_password"]) >= 12
