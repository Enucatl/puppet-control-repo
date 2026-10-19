"""Check offline-root ceremony identity and revocation boundaries."""

import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest


@pytest.fixture
def ceremony(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Load the ceremony with a fake established offline authority."""
    script = Path(__file__).resolve().parents[1] / "scripts/openvox_offline_root.py"
    spec = importlib.util.spec_from_file_location("offline_root", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []
    signatures = []

    def execute(arguments):
        """Expose only the selected local daemon and isolated root state."""
        calls.append(("execute", arguments))
        if "info" in arguments:
            return socket.gethostname()
        if "{{.HostConfig.NetworkMode}}" in arguments:
            return "none"
        return "false"

    def api(service, method, path, body=None):
        """Record mutations without returning any administrative secret."""
        calls.append((method, path, body))
        if path == "pki/cert/ca":
            return {"data": {"certificate": "root"}}
        if path == "pki/cert/crl":
            return {"data": {"certificate": "signed-crl"}}
        if path == "pki/config/urls":
            return {"data": {"issuing_certificates": [], "crl_distribution_points": []}}
        if path == "pki/root/sign-intermediate":
            signatures.append(body)
            return {
                "data": {
                    "certificate": f"cert-{len(signatures)}",
                    "serial_number": str(len(signatures)),
                }
            }
        if path == "pki/crl/rotate":
            return {"data": {"success": True}}
        assert (method, path, body) == ("POST", "pki/revoke", {"serial_number": "1"})
        return {}

    def inside(service, arguments, data=None):
        """Return public CSR, verified metadata or public DER bytes only."""
        if module.GENERATE in arguments:
            return "new-csr"
        if module.VERIFY in arguments:
            return json.dumps(
                {
                    "root_signature": True,
                    "root_crl_signature": True,
                    "crl_next_update": "2099-01-01T00:00:00Z",
                }
            )
        return base64.b64encode(b"signed-der-crl").decode() + "\n"

    runtime = SimpleNamespace(
        WORK=tmp_path,
        DOCKER=["docker", "--host", "unix:///var/run/docker.sock"],
        PROJECT="openvox-rehearsal",
        RUBY="ruby",
        execute=execute,
        api=api,
        inside=inside,
        start_vault=lambda service: calls.append(("start", service)),
        compose=lambda *arguments: calls.append(("compose", arguments)),
    )
    monkeypatch.setattr(module.ssl, "PEM_cert_to_DER_cert", lambda pem: b"root-der")
    return module, runtime, calls, signatures


def test_rehearsal_renews_same_csr_and_revokes_only_its_fresh_certificate(ceremony):
    """Never accept an operator-provided serial or revoke active authorities."""
    module, runtime, calls, signatures = ceremony
    report = module.ceremony(
        "rehearse", hashlib.sha256(b"root-der").hexdigest(), runtime
    )
    assert signatures == [
        {"csr": "new-csr", "format": "pem", "ttl": "48h", "max_path_length": 0},
        {"csr": "new-csr", "format": "pem", "ttl": "72h", "max_path_length": 0},
    ]
    assert ("POST", "pki/revoke", {"serial_number": "1"}) in calls
    assert ("compose", ("stop", "root")) in calls
    output = Path(report["public_artifacts"])
    assert (output / "root.crl").read_bytes() == b"signed-der-crl"
    assert sorted(path.name for path in output.iterdir()) == [
        "evidence.json",
        "intermediate-initial.pem",
        "intermediate-renewed.pem",
        "root.crl",
        "root.crl.pem",
        "root.pem",
    ]
    assert all(path.stat().st_mode & 0o777 == 0o644 for path in output.iterdir())


def test_publish_rotates_crl_without_signing_or_revoking_certificates(ceremony):
    """A periodic CRL refresh does not replace any certificate or authority."""
    module, runtime, calls, signatures = ceremony
    module.ceremony("publish", hashlib.sha256(b"root-der").hexdigest(), runtime)
    assert signatures == []
    assert not any(call[0] == "POST" for call in calls)
    assert ("GET", "pki/crl/rotate", None) in calls


def test_root_mismatch_stops_root_before_mutations(ceremony):
    """A changed root cannot silently establish new trust."""
    module, runtime, calls, signatures = ceremony
    with pytest.raises(RuntimeError, match="fingerprint mismatch"):
        module.ceremony("rehearse", "0" * 64, runtime)
    assert signatures == []
    assert ("compose", ("stop", "root")) in calls
    assert not any(call[0] == "POST" for call in calls)
    assert not (runtime.WORK / "public").exists()


def test_missing_authority_stops_without_initialization(ceremony):
    """Missing state propagates; the ceremony never requests fresh initialization."""
    module, runtime, calls, signatures = ceremony

    def missing(service):
        """Model the controller's refusal to initialize a missing identity."""
        raise RuntimeError("Missing established Vault storage")

    runtime.start_vault = missing
    with pytest.raises(RuntimeError, match="Missing established"):
        module.ceremony("rehearse", "0" * 64, runtime)
    assert signatures == []
    assert ("compose", ("stop", "root")) in calls


def test_networked_root_is_refused(ceremony):
    """A misplaced root cannot run a ceremony on an online Docker network."""
    module, runtime, calls, _ = ceremony
    original = runtime.execute
    runtime.execute = lambda args: (
        "bridge" if "{{.HostConfig.NetworkMode}}" in args else original(args)
    )
    with pytest.raises(RuntimeError, match="network_mode none"):
        module.ceremony("rehearse", "0" * 64, runtime)
    assert not any(call[0] == "start" for call in calls)


def test_remote_daemon_is_refused(ceremony):
    """A daemon with another hostname cannot enter the ceremony."""
    module, runtime, calls, _ = ceremony
    runtime.execute = lambda args: "docker.home.arpa"
    with pytest.raises(RuntimeError, match="local Docker daemon"):
        module.ceremony("rehearse", "0" * 64, runtime)
    assert calls == []


def test_failed_verification_stops_root_without_publishing(ceremony):
    """Do not publish unchecked root CRLs or leave the authority running."""
    module, runtime, calls, _ = ceremony
    original = runtime.inside

    def failed(service, arguments, data=None):
        """Model a failed cryptographic verification before file publication."""
        if module.VERIFY in arguments:
            raise RuntimeError("Root CRL signature invalid")
        return original(service, arguments, data)

    runtime.inside = failed
    with pytest.raises(RuntimeError, match="CRL signature invalid"):
        module.ceremony("publish", hashlib.sha256(b"root-der").hexdigest(), runtime)
    assert ("compose", ("stop", "root")) in calls
    assert not (runtime.WORK / "public").exists()


@pytest.mark.parametrize("fingerprint", ["", "a" * 63, "G" * 64])
def test_invalid_fingerprint_never_contacts_docker(ceremony, fingerprint):
    """Require a complete explicit authority fingerprint before using Docker."""
    module, runtime, calls, _ = ceremony
    with pytest.raises(ValueError, match="64 lowercase hex"):
        module.ceremony("rehearse", fingerprint, runtime)
    assert calls == []
