"""Check Atlas enrollment boundaries without contacting live services."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
from types import ModuleType
from unittest.mock import Mock

import pytest


@pytest.fixture
def enrollment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Load the controller with private temporary state and inert commands."""
    spec = importlib.util.spec_from_file_location(
        "atlas_enroll", Path(__file__).resolve().parents[1] / "proxmox/atlas-enroll.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.infra, "STATE", tmp_path)
    monkeypatch.setattr(module, "PUPPET_CA", tmp_path / "puppet-ca.pem")
    monkeypatch.setattr(module, "SYSTEM_CA", tmp_path / "system-ca.crt")
    monkeypatch.setattr(module, "CERTIFICATE", tmp_path / "atlas.pem")
    for name in ("run", "api", "openvox", "git"):
        monkeypatch.setattr(module.infra, name, Mock(name=name))
    module.infra.openvox.return_value = "puppet-chain"
    module.infra.git.return_value = "published-sha\n"
    return module


def test_import_is_required(enrollment: ModuleType) -> None:
    """No token or host configuration changes precede verified identity import."""
    with pytest.raises(RuntimeError, match="final IPA import"):
        enrollment.enroll()
    enrollment.infra.api.assert_not_called()
    enrollment.infra.run.assert_not_called()


def test_helper_accepts_token_stdin() -> None:
    """Exercise the helper's real input parser without any host operations."""
    helper = Path(__file__).resolve().parents[1] / "proxmox/configure-puppet.sh"
    prefix = helper.read_text().split('CERTNAME="${3:?certname required}"', 1)[0]
    result = subprocess.run(
        [
            "bash",
            "-c",
            prefix + '\nprintf "%s" "$VAULT_TOKEN"',
            "helper",
            "docker",
            "-",
        ],
        input="private-token\n",
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout == "private-token"


def test_trust_conflict_is_not_overwritten(enrollment: ModuleType) -> None:
    """Refuse a different existing CA before writing either destination."""
    (enrollment.infra.STATE / "root.pem").write_text("new-root")
    enrollment.SYSTEM_CA.write_text("other-root")
    with pytest.raises(RuntimeError, match="Different existing trust"):
        enrollment.install_trust()
    assert not enrollment.PUPPET_CA.exists()
    assert enrollment.SYSTEM_CA.read_text() == "other-root"


def test_runtime_policy_is_scoped(enrollment: ModuleType) -> None:
    """Only Atlas receives its wildcard namespace; the policy grants no KV."""
    enrollment.infra.api.return_value = {
        "data": {"cert/": {"accessor": "cert_accessor"}}
    }
    enrollment.configure_runtime()
    calls = enrollment.infra.api.call_args_list
    roles = {call.args[1]: call.args[2] for call in calls if "/roles/" in call.args[1]}
    assert set(roles) == {
        "pki_int/roles/puppet",
        "pki_int/roles/puppet-atlas.home.arpa",
    }
    assert roles["pki_int/roles/puppet"]["allowed_domains_template"] is True
    assert roles["pki_int/roles/puppet"]["allow_wildcard_certificates"] is False
    role = roles["pki_int/roles/puppet-atlas.home.arpa"]
    assert role["allowed_domains"] == ["atlas.home.arpa"]
    assert role["key_type"] == "ml-dsa"
    assert role["parameter_set"] == "ml-dsa-44"
    assert role["key_usage"] == ["DigitalSignature"]
    assert role["allow_ip_sans"] is False
    policy = next(
        call.args[2]["policy"]
        for call in calls
        if call.args[1] == "sys/policies/acl/puppet"
    )
    assert "kv/" not in policy
    assert "{{identity.entity.aliases.cert_accessor.name}}" in policy
    auth = calls[-1].args[2]
    assert auth["allowed_common_names"] == ["atlas.home.arpa", "aperture.home.arpa"]
    assert auth["allowed_dns_sans"] == ["atlas.home.arpa", "aperture.home.arpa"]
    assert auth["token_policies"] == ["puppet"]


def test_token_uses_stdin(
    enrollment: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The enrollment credential reaches only the existing helper's stdin."""
    (enrollment.infra.STATE / "freeipa").mkdir()
    (enrollment.infra.STATE / "freeipa/imported.json").write_text('{"success":true}')
    monkeypatch.setattr(enrollment, "install_trust", Mock())
    monkeypatch.setattr(enrollment, "configure_runtime", Mock())
    token = {
        "client_token": "private-token",
        "policies": ["puppet-enrollment"],
        "metadata": {"certname": "atlas.home.arpa"},
        "num_uses": 1,
        "orphan": True,
        "renewable": False,
        "token_type": "service",
        "lease_duration": 7200,
    }
    enrollment.infra.api.return_value = {"auth": token}
    enrollment.infra.run.return_value = "catalog applied"
    enrollment.enroll()
    command, payload = enrollment.infra.run.call_args.args
    assert "private-token" not in repr(command)
    assert command[-4:] == ["docker", "-", "atlas.home.arpa", "openvox.home.arpa"]
    assert payload == "private-token\n"
    assert (
        json.loads((enrollment.infra.STATE / "atlas-enrolled.json").read_text())[
            "certname"
        ]
        == "atlas.home.arpa"
    )


def test_existing_certificate_resumes_catalog(
    enrollment: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retry a partial first catalog without issuing another enrollment token."""
    (enrollment.infra.STATE / "freeipa").mkdir()
    (enrollment.infra.STATE / "freeipa/imported.json").write_text('{"success":true}')
    enrollment.CERTIFICATE.write_text("existing certificate")
    monkeypatch.setattr(enrollment, "install_trust", Mock())
    monkeypatch.setattr(enrollment, "configure_runtime", Mock())
    enrollment.infra.run.return_value = "catalog applied"
    enrollment.enroll()
    enrollment.infra.api.assert_not_called()
    assert enrollment.infra.run.call_args.args[0][-3:] == [
        "--apply",
        "atlas.home.arpa",
        "openvox.home.arpa",
    ]


@pytest.mark.parametrize("already_enrolled", [False, True])
def test_shared_installer_precedes_enrollment(
    enrollment: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    already_enrolled: bool,
) -> None:
    """Both enrollment paths provision through signed APT after trust checks."""
    (enrollment.infra.STATE / "freeipa").mkdir()
    (enrollment.infra.STATE / "freeipa/imported.json").write_text('{"success":true}')
    if already_enrolled:
        enrollment.CERTIFICATE.write_text("existing certificate")
    events = []

    def command(argv: list[str], payload: str | None = None) -> str:
        del payload
        events.append(argv)
        return "catalog applied"

    def token(*args: object) -> dict:
        events.append(list(args))
        return {
            "auth": {
                "client_token": "private-token",
                "policies": ["puppet-enrollment"],
                "metadata": {"certname": "atlas.home.arpa"},
                "num_uses": 1,
                "orphan": True,
                "renewable": False,
                "token_type": "service",
                "lease_duration": 7200,
            }
        }

    enrollment.infra.run.side_effect = command
    enrollment.infra.api.side_effect = token
    monkeypatch.setattr(enrollment, "install_trust", lambda: events.append(["trust"]))
    monkeypatch.setattr(
        enrollment, "configure_runtime", lambda: events.append(["runtime"])
    )
    enrollment.enroll()
    helper = str(enrollment.infra.SOURCE / "proxmox/configure-puppet.sh")
    assert events[:4] == [
        ["bash", helper, "--preflight", "atlas.home.arpa"],
        ["trust"],
        ["bash", str(enrollment.infra.SOURCE / "proxmox/install-openvox.sh")],
        ["runtime"],
    ]
    assert all("install-atlas-" not in repr(event) for event in events)
    assert not (enrollment.infra.STATE / "curl").exists()
    assert not (enrollment.infra.STATE / "ipalib").exists()
    if already_enrolled:
        assert enrollment.CERTIFICATE.read_text() == "existing certificate"
        enrollment.infra.api.assert_not_called()
        assert events[4:] == [
            ["bash", helper, "--apply", "atlas.home.arpa", "openvox.home.arpa"]
        ]
    else:
        assert events[4][0:2] == ["POST", "auth/token/create-orphan"]
        assert events[5] == [
            "bash",
            helper,
            "docker",
            "-",
            "atlas.home.arpa",
            "openvox.home.arpa",
        ]
