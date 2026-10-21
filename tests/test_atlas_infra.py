"""Exercise Atlas authority lifecycle boundaries without containers or Vault."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
from types import ModuleType
from unittest.mock import Mock

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.x509.oid import NameOID
import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("data", [None, "explicit child input"])
def test_commands_do_not_consume_caller_input(data: str | None) -> None:
    """Docker/SSH children must leave later shell commands on the parent's stdin."""
    program = """
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location('infra', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
child = module.run([sys.executable, '-c', 'import sys; print(sys.stdin.read())'], json.loads(sys.argv[2]))
print(json.dumps({'child': child, 'remaining': sys.stdin.read()}))
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            program,
            str(ROOT / "proxmox/atlas-infra.py"),
            json.dumps(data),
        ],
        input="remaining verification commands",
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(result.stdout) == {
        "child": (data or "") + "\n",
        "remaining": "remaining verification commands",
    }


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
    for name in ("VAULT_SECRETS", "IPA_SECRETS"):
        path = tmp_path / name.lower()
        path.mkdir()
        monkeypatch.setattr(module, name, path)
    monkeypatch.setattr(module, "CA", module.VAULT_SECRETS / "ca.pem")
    monkeypatch.setattr(module.os, "chown", Mock())
    monkeypatch.setattr(module, "MAINTENANCE_LOCK", tmp_path / "maintenance.lock")
    for name in ("run", "compose", "api", "openvox", "volume_file"):
        monkeypatch.setattr(module, name, Mock(name=name))
    return module


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
    """Existing recovery credentials never reenter fresh initialization."""
    (controller.VAULT_SECRETS / "recovery.json").write_text("{}")
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
    """Resume a sealed-state checkpoint using fake existing named volumes."""
    state = controller.STATE
    (state / "initializing").write_text("atlas.home.arpa\n")
    bootstrap = controller.STATE / "transport"
    bootstrap.mkdir()
    for name, value in (
        ("tls.key", "saved temporary listener key"),
        ("tls.crt", "saved temporary listener certificate"),
        ("trust.pem", "saved temporary listener trust"),
    ):
        (bootstrap / name).write_text(value)
        (bootstrap / name).chmod(0o600)
    controller.CA.write_text("saved temporary listener trust")
    (controller.VAULT_SECRETS / "recovery.json").write_text(
        json.dumps({"root_token": "test-root-token", "keys_base64": ["test-share"]})
    )
    (controller.VAULT_SECRETS / "recovery.json").chmod(0o600)
    names = ["openvox_" + name for name in controller.OPENVOX_VOLUMES]
    names += list(controller.VAULT_VOLUMES)

    def command(arguments: list[str], data: str | None = None) -> str:
        if arguments == controller.DOCKER + ["volume", "ls", "--format", "{{.Name}}"]:
            return "\n".join("infra_" + name for name in names)
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
    """Temporary TLS preparation preserves any already published final PEMs."""
    if final_saved:
        (resumed.VAULT_SECRETS / "key.pem").write_text("saved final key")
        (resumed.VAULT_SECRETS / "fullchain.pem").write_text("saved final chain")
        resumed.CA.write_text("saved root")
    resumed.install_transport()
    assert (resumed.VAULT_SECRETS / "key.pem").read_text() == (
        "saved final key" if final_saved else "saved temporary listener key"
    )
    assert (resumed.VAULT_SECRETS / "fullchain.pem").read_text() == (
        "saved final chain" if final_saved else "saved temporary listener certificate"
    )
    assert resumed.CA.read_text() == (
        "saved root" if final_saved else "saved temporary listener trust"
    )
    resumed.volume_file.assert_not_called()
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
    resumed.CA.write_text("original authority")
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
    assert resumed.CA.read_text() == "original authority"
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
        resumed.CA.write_text("saved authority certificate")

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


def test_bootstrap_publishes_native_pems_and_recovery_secrets(
    resumed: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bootstrap produces the same canonical PEM files Puppet later manages."""
    monkeypatch.setattr(resumed.ssl, "PEM_cert_to_DER_cert", lambda _: b"root")
    monkeypatch.setattr(resumed, "services_intermediate", Mock())
    monkeypatch.setattr(resumed, "configure_access", Mock())
    monkeypatch.setattr(resumed, "deploy_initial", Mock())
    command = resumed.run.side_effect
    resumed.run.side_effect = lambda args, data=None: (
        "PUBLIC-KEY" if args[0] == resumed.RUBY else command(args, data)
    )
    resumed.openvox.return_value = "imported"
    responses = {
        ("GET", "sys/mounts"): {"data": {"pki/": {}}},
        ("LIST", "pki/issuers"): {"data": {"keys": ["root-issuer"]}},
        ("GET", "pki/cert/ca"): {"data": {"certificate": "ROOT-CERT"}},
        ("GET", "pki/cert/crl"): {"data": {"certificate": "ROOT-CRL"}},
        ("POST", "pki_int/issue/vault-transport"): {
            "data": {
                "certificate": "LEAF",
                "private_key": "PRIVATE-KEY",
                "ca_chain": ["ISSUER", "ROOT-CERT"],
            }
        },
    }
    resumed.api.side_effect = lambda method, path, *args, **kwargs: responses.get(
        (method, path), {}
    )
    resumed.initialize()
    secrets = resumed.VAULT_SECRETS
    assert (secrets / "fullchain.pem").read_text() == "LEAF\nISSUER\nROOT-CERT\n"
    assert (secrets / "key.pem").read_text() == "PRIVATE-KEY"
    assert (secrets / "public/vault.pub.pem").read_text() == "PUBLIC-KEY"
    assert (secrets / "root-token").read_text() == "test-root-token"
    assert resumed.CA.read_text() == "ROOT-CERT"
    assert not (resumed.STATE / "ready.json").exists()
    assert not (resumed.STATE / "vault-transport.json").exists()
    assert not (resumed.STATE / "initializing").exists()
    assert all(
        path.stat().st_mode & 0o777 == 0o600
        for path in secrets.rglob("*")
        if path.is_file()
    )


@pytest.mark.parametrize("established", [False, True])
def test_access_setup_scopes_compiler_and_preserves_existing_data(
    controller: ModuleType,
    established: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Load shared policies without shell source and preserve scoped access."""
    policies = tmp_path / "docker/vault/scripts/policies"
    policies.mkdir(parents=True)
    for name in ("puppet-server", "puppet-enrollment"):
        (policies / f"{name}.hcl").write_text(
            (ROOT / f"docker/vault/scripts/policies/{name}.hcl").read_text()
        )
    monkeypatch.setattr(controller, "SOURCE", tmp_path)
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
        "sys/policies/acl/atlas-transport",
        "auth/cert/certs/atlas-transport",
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
    transport = writes["auth/cert/certs/atlas-transport"]
    assert transport["allowed_common_names"] == ["atlas.home.arpa"]
    assert transport["token_policies"] == ["atlas-transport"]
    assert re.findall(
        r'path "([^"]+)"', writes["sys/policies/acl/atlas-transport"]["policy"]
    ) == [
        "pki_int/issue/vault-transport",
        "pki_freeipa/issue/ipa-transport",
        "auth/token/lookup-self",
    ]
    for name, path in (
        ("puppet-server", "kv/data/puppet"),
        ("puppet-enrollment", "auth/token/lookup-self"),
    ):
        policy = writes[f"sys/policies/acl/{name}"]["policy"]
        assert re.findall(r'path "([^"]+)"', policy) == [path]
        assert re.findall(r"capabilities\s*=\s*\[([^]]+)\]", policy) == ['"read"']


@pytest.fixture
def intermediate(controller: ModuleType) -> tuple[ModuleType, dict]:
    """Model the saved request, original key and a real signed certificate."""
    key = ed25519.Ed25519PrivateKey.generate()
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Services")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(42)
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, None)
        .public_bytes(serialization.Encoding.PEM)
        .decode()
    )
    state = {
        "issuers": [],
        "keys": ["original-key"],
        "imports": [],
        "signed": 0,
        "certificate": certificate,
        "key_id": "original-key",
        "issuer_id": "original-issuer",
    }
    (controller.STATE / "vault-intermediate.json").write_text(
        json.dumps({"csr": "saved CSR", "key_id": "original-key"})
    )

    def response(method: str, path: str, body: dict | None = None, **kwargs) -> dict:
        """Only permit the expected initialization API operations."""
        if path == "sys/mounts":
            return {"data": {"pki_int/": {}}}
        if path == "pki_int/issuers":
            return {"data": {"keys": state["issuers"]}}
        if path == "pki_int/keys":
            return {"data": {"keys": state["keys"]}}
        if path == "pki/root/sign-intermediate":
            state["signed"] += 1
            return {"data": {"certificate": certificate}}
        if path == "pki_int/intermediate/set-signed":
            assert (controller.STATE / "vault-intermediate.pem").read_text() == body[
                "certificate"
            ]
            state["imports"].append(body["certificate"])
            if state.get("fail_import"):
                raise RuntimeError("interrupted import")
            state["issuers"] = ["original-issuer"]
            return {}
        if path == "pki_int/issuer/default":
            return {
                "data": {
                    name: state[name] for name in ("certificate", "key_id", "issuer_id")
                }
            }
        raise AssertionError(f"Unexpected API operation: {method} {path}")

    controller.api.side_effect = response
    return controller, state


def test_interrupted_services_import_reuses_exact_saved_certificate(
    intermediate: tuple,
) -> None:
    """An import retry never signs the saved CSR for a second certificate."""
    controller, state = intermediate
    state["fail_import"] = True
    with pytest.raises(RuntimeError, match="interrupted import"):
        controller.services_intermediate()
    state["fail_import"] = False
    controller.services_intermediate()
    controller.services_intermediate()
    assert state["signed"] == 1
    assert state["imports"] == [state["certificate"], state["certificate"]]
    assert json.loads(
        (controller.STATE / "vault-intermediate-established.json").read_text()
    ) == {
        "key_id": "original-key",
        "issuer_id": "original-issuer",
    }


@pytest.mark.parametrize("missing", ["issuer", "key", "request", "certificate"])
def test_established_services_state_requires_recovery(
    intermediate: tuple, missing: str
) -> None:
    """Losing any established state cannot generate or import a replacement."""
    controller, state = intermediate
    controller.services_intermediate()
    controller.api.reset_mock()
    if missing == "issuer":
        state["issuers"] = []
    elif missing == "key":
        state["keys"] = []
    elif missing == "request":
        (controller.STATE / "vault-intermediate.json").unlink()
    else:
        (controller.STATE / "vault-intermediate.pem").unlink()
    with pytest.raises(RuntimeError, match="[Mm]issing|[Rr]ecover"):
        controller.services_intermediate()
    assert all(
        call.args[0] == "GET" or call.args[0] == "LIST"
        for call in controller.api.call_args_list
    )
    assert state["signed"] == 1
    assert len(state["imports"]) == 1


@pytest.mark.parametrize("changed", ["certificate", "key_id", "issuer_id"])
def test_services_issuer_and_associated_key_are_verified(
    intermediate: tuple, changed: str
) -> None:
    """An existing issuer must retain its exact certificate, ID and original key."""
    controller, state = intermediate
    controller.services_intermediate()
    if changed == "certificate":
        state[changed] = (
            "-----BEGIN CERTIFICATE-----\nY2hhbmdlZA==\n-----END CERTIFICATE-----\n"
        )
    else:
        state[changed] = "replacement"
    with pytest.raises(RuntimeError, match="issuer.*changed"):
        controller.services_intermediate()
    assert state["signed"] == 1
    assert len(state["imports"]) == 1


def test_services_refuses_missing_established_mount(intermediate: tuple) -> None:
    """A lost established mount cannot silently become an empty replacement."""
    controller, _state = intermediate
    controller.api.side_effect = None
    controller.api.return_value = {"data": {}}
    with pytest.raises(RuntimeError, match="mount missing"):
        controller.services_intermediate()
    controller.api.assert_called_once_with("GET", "sys/mounts")


def test_new_services_request_preserves_the_generated_key(intermediate: tuple) -> None:
    """Fresh generation records the key and persists the certificate before import."""
    controller, state = intermediate
    request = controller.STATE / "vault-intermediate.json"
    request.unlink()
    state["keys"] = []
    existing_response = controller.api.side_effect

    def response(method: str, path: str, body: dict | None = None, **kwargs) -> dict:
        """Generate once, then delegate the remaining fake Vault operations."""
        if path == "pki_int/intermediate/generate/internal":
            assert not request.exists()
            state["keys"] = ["original-key"]
            return {"data": {"csr": "generated CSR", "key_id": "original-key"}}
        return existing_response(method, path, body, **kwargs)

    controller.api.side_effect = response
    controller.services_intermediate()
    assert json.loads(request.read_text())["key_id"] == "original-key"
    assert state["signed"] == 1
    assert state["imports"] == [state["certificate"]]
