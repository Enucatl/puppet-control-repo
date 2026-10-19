"""Exercise Atlas authority lifecycle boundaries without containers or Vault."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import ssl
from types import ModuleType
from unittest.mock import Mock

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.x509.oid import NameOID
import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def controller(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Import the controller with private disposable state and inert commands."""
    spec = importlib.util.spec_from_file_location(
        "atlas_infra", ROOT / "proxmox/atlas-infra.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "STATE", tmp_path)
    for name in ("run", "compose", "api", "openvox", "volume_file"):
        monkeypatch.setattr(module, name, Mock(name=name))
    return module


def test_start_refuses_missing_checkpoint_before_any_mutation(
    controller: ModuleType,
) -> None:
    """A normal start cannot silently initialize an empty authority."""
    with pytest.raises(RuntimeError, match="Missing initialized state"):
        controller.start()
    controller.run.assert_not_called()
    controller.compose.assert_not_called()
    controller.api.assert_not_called()
    assert not list(controller.STATE.iterdir())


def test_start_only_starts_established_services(controller: ModuleType) -> None:
    """Existing state starts without issuing credentials or altering PKI."""
    (controller.STATE / "ready.json").write_text("{}")
    controller.start()
    controller.compose.assert_called_once_with(
        "up",
        "-d",
        "--no-deps",
        "--wait",
        "--wait-timeout",
        "300",
        "vault",
        "vault-unsealer",
        "openvox",
    )
    controller.api.assert_not_called()
    controller.openvox.assert_not_called()
    controller.volume_file.assert_not_called()


@pytest.mark.parametrize(
    "volume",
    [
        "openvox_ssl",
        "openvox_ca",
        "openvox_code",
        "openvox_data",
        "openvox_gems",
        "openvox_git",
        "openvox_r10k_cache",
        "openvox_trust",
        "vault_data",
        "vault_logs",
        "certificates",
        "vault_unseal",
    ],
)
def test_initialize_refuses_existing_state_without_checkpoint(
    controller: ModuleType, volume: str
) -> None:
    """Any protected volume blocks treating an existing installation as fresh."""
    controller.run.return_value = f"unrelated_volume\ninfra_{volume}\n"
    with pytest.raises(RuntimeError, match="Existing infrastructure state"):
        controller.initialize()
    controller.run.assert_called_once_with(
        controller.DOCKER + ["volume", "ls", "--format", "{{.Name}}"]
    )
    controller.compose.assert_not_called()
    controller.api.assert_not_called()
    assert not (controller.STATE / "initializing").exists()


def test_initialize_refuses_completed_installation(controller: ModuleType) -> None:
    """A completed checkpoint never reenters fresh initialization."""
    (controller.STATE / "ready.json").write_text("{}")
    with pytest.raises(RuntimeError, match="Established infrastructure"):
        controller.initialize()
    controller.run.assert_not_called()
    controller.api.assert_not_called()


def test_resume_refuses_lost_allocated_volume(controller: ModuleType) -> None:
    """An interrupted setup must restore disappeared volumes, not recreate them."""
    (controller.STATE / "initializing").write_text("atlas.home.arpa\n")
    (controller.STATE / "volumes-created").write_text("vault_data\n")
    controller.run.return_value = ""
    with pytest.raises(RuntimeError, match="Established volume missing"):
        controller.initialize()
    assert controller.run.call_count == 1
    controller.compose.assert_not_called()


@pytest.fixture
def resumed(controller: ModuleType, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Resume a sealed-state checkpoint using fake existing labeled volumes."""
    state = controller.STATE
    (state / "initializing").write_text("atlas.home.arpa\n")
    (state / "transport").mkdir()
    (state / "transport/tls.key").write_text("saved temporary listener key")
    (state / "transport/tls.crt").write_text("saved temporary listener certificate")
    (state / "transport/trust.pem").write_text("saved temporary listener trust")
    (state / "trust.pem").write_text("saved temporary listener trust")
    (state / "recovery.json").write_text(
        json.dumps({"root_token": "test-root-token", "keys_base64": ["test-share"]})
    )
    names = ["openvox_" + name for name in controller.OPENVOX_VOLUMES]
    names += list(controller.VAULT_VOLUMES)

    def command(arguments: list[str], data: str | None = None) -> str:
        if arguments == controller.DOCKER + ["volume", "ls", "--format", "{{.Name}}"]:
            return "\n".join("infra_" + name for name in names)
        if arguments[: len(controller.DOCKER) + 2] == controller.DOCKER + [
            "volume",
            "inspect",
        ]:
            return json.dumps([{"Labels": {"home.arpa.authority": "atlas"}}])
        if arguments[: len(controller.DOCKER) + 1] == controller.DOCKER + ["run"]:
            return ""
        raise AssertionError(f"Unexpected command: {arguments}")

    controller.run.side_effect = command
    monkeypatch.setattr(
        controller, "health", Mock(return_value={"initialized": True, "sealed": False})
    )
    return controller


@pytest.mark.parametrize("final_saved", [False, True])
def test_transport_retry_repairs_saved_identity_and_retains_transition_trust(
    resumed: ModuleType, final_saved: bool
) -> None:
    """Interrupted TLS writes are repaired from the same saved identity."""
    if final_saved:
        (resumed.STATE / "vault-transport.json").write_text(
            json.dumps(
                {
                    "private_key": "saved final key",
                    "certificate": "saved final leaf",
                    "ca_chain": ["saved intermediate", "saved root"],
                }
            )
        )
        (resumed.STATE / "root.pem").write_text("saved root")
    resumed.install_transport()
    writes = {
        (call.args[0], call.args[1]): call.args[2]
        for call in resumed.volume_file.call_args_list
    }
    assert len(writes) == 3
    assert writes["certificates", "vault.key"] == (
        "saved final key" if final_saved else "saved temporary listener key"
    )
    assert writes["certificates", "vault_chain.crt"] == (
        "saved final leaf\nsaved intermediate\nsaved root"
        if final_saved
        else "saved temporary listener certificate"
    )
    assert writes["certificates", "ca.crt"] == (
        "saved root" if final_saved else "saved temporary listener trust"
    )
    trust = (resumed.STATE / "trust.pem").read_text()
    assert "saved temporary listener trust" in trust
    if final_saved:
        assert "saved root" in trust
    resumed.api.assert_not_called()
    resumed.run.assert_not_called()


def test_resume_rejects_changed_root_before_import(resumed: ModuleType) -> None:
    """A different returned CA cannot overwrite the pinned root or reach import."""
    key = ed25519.Ed25519PrivateKey.generate()
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Replacement root")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, None)
        .public_bytes(serialization.Encoding.PEM)
        .decode()
    )
    original_pin = hashlib.sha256(b"original authority").hexdigest()
    assert (
        hashlib.sha256(ssl.PEM_cert_to_DER_cert(certificate)).hexdigest()
        != original_pin
    )
    (resumed.STATE / "root.sha256").write_text(original_pin)
    responses = {
        ("GET", "sys/mounts"): {"data": {"pki/": {"type": "pki"}}},
        ("LIST", "pki/issuers"): {"data": {"keys": ["existing-issuer"]}},
        ("GET", "pki/cert/ca"): {"data": {"certificate": certificate}},
    }
    resumed.api.side_effect = lambda method, path, *args, **kwargs: responses[
        method, path
    ]
    with pytest.raises(RuntimeError, match="root identity changed"):
        resumed.initialize()
    assert (resumed.STATE / "root.sha256").read_text() == original_pin
    assert certificate not in (resumed.STATE / "trust.pem").read_text()
    resumed.openvox.assert_not_called()
    assert all(
        call.args[0] != "openvox_trust" for call in resumed.volume_file.call_args_list
    )


@pytest.mark.parametrize("saved_pin", [True, False])
def test_resume_never_regenerates_a_missing_pinned_root(
    resumed: ModuleType, saved_pin: bool
) -> None:
    """A saved authority pin requires recovery if Vault has lost its issuer."""
    if saved_pin:
        (resumed.STATE / "root.sha256").write_text("a" * 64)

    def response(method: str, path: str, *args, **kwargs) -> dict:
        if (method, path) == ("GET", "sys/mounts"):
            return {"data": {"pki/": {"type": "pki"}}}
        if (method, path) == ("LIST", "pki/issuers"):
            return {"errors": []}
        if (method, path) == ("LIST", "pki/keys"):
            return {"data": {"keys": [] if saved_pin else ["existing-root-key"]}}
        raise AssertionError(f"Unexpected mutation or lookup: {method} {path}")

    resumed.api.side_effect = response
    with pytest.raises(RuntimeError, match="[Rr]estor|[Rr]ecover|[Mm]issing"):
        resumed.initialize()
    assert not (resumed.STATE / "ready.json").exists()


@pytest.mark.parametrize("established", [False, True])
def test_access_setup_scopes_compiler_and_preserves_existing_data(
    controller: ModuleType, established: bool
) -> None:
    """Create only absent mounts and authorize only the new compiler identity."""
    responses = {
        ("GET", "sys/mounts"): {"data": {"kv/": {}} if established else {}},
        ("GET", "kv/data/puppet"): {
            "data": {"data": {"existing": "retained"}} if established else None
        },
        ("GET", "sys/auth"): {"data": {"cert/": {}} if established else {}},
    }
    controller.api.side_effect = lambda method, path, *args, **kwargs: responses.get(
        (method, path), {}
    )
    controller.openvox.return_value = "authenticated OpenVox intermediate chain"
    controller.configure_access()
    writes = {
        call.args[1]: call.args[2]
        for call in controller.api.call_args_list
        if call.args[0] in {"POST", "PUT"}
    }
    expected = {
        "sys/policies/acl/puppet-server",
        "sys/policies/acl/puppet-enrollment",
        "auth/cert/certs/puppet-server",
    }
    if not established:
        expected |= {"sys/mounts/kv", "kv/data/puppet", "sys/auth/cert"}
        assert writes["sys/mounts/kv"] == {"type": "kv", "options": {"version": "2"}}
    assert set(writes) == expected
    role = writes["auth/cert/certs/puppet-server"]
    assert role["allowed_common_names"] == ["openvox.home.arpa"]
    assert role["allowed_dns_sans"] == ["openvox.home.arpa"]
    assert role["certificate"] == controller.openvox.return_value
    assert role["token_policies"] == ["puppet-server"]
    assert role["token_no_default_policy"] is True
    for name, path in (
        ("puppet-server", "kv/data/puppet"),
        ("puppet-enrollment", "auth/token/lookup-self"),
    ):
        policy = writes[f"sys/policies/acl/{name}"]["policy"]
        assert re.findall(r'path "([^"]+)"', policy) == [path]
        assert re.findall(r"capabilities\s*=\s*\[([^]]+)\]", policy) == ['"read"']
