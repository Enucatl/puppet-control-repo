"""Check browser policies add only the CAs installed by FreeIPA enrollment."""

import base64
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import ssl

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
import pytest

from puppet_catalog import ROOT, compile_catalog, resources

SPEC = importlib.util.spec_from_file_location(
    "browser_trust", ROOT / "modules/freeipa_firefox/files/browser-trust.py"
)
assert SPEC and SPEC.loader
TRUST = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TRUST)
BASELINE = ROOT / "modules/freeipa_firefox/files/firefox-policies.json"


def certificate(name: str, *, ca: bool = True) -> bytes:
    """Create a real certificate so tests exercise OpenSSL's CA selection."""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.now(timezone.utc)
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
        .public_bytes(serialization.Encoding.PEM)
    )


def test_browser_policies_use_only_enrolled_ipa_cas(
    tmp_path: Path,
) -> None:
    """Exclude public/bootstrap roots, handle IPA comments, and repair outputs."""
    ipa_ca_dir = tmp_path / "ca-certificates/ipa-ca"
    ipa_ca_dir.mkdir(parents=True)
    roots = [certificate("First CA"), certificate("Second CA")]
    (ipa_ca_dir / "first.crt").write_bytes(b"# FreeIPA CA\n" + roots[0])
    (ipa_ca_dir / "second.crt").write_bytes(
        roots[1] + roots[0] + certificate("Leaf", ca=False)
    )
    (ipa_ca_dir.parent / "atlas-infrastructure.crt").write_bytes(
        certificate("Bootstrap")
    )
    (tmp_path / "ca-certificates.crt").write_bytes(certificate("Public CA"))
    firefox = tmp_path / "firefox"
    chromium = tmp_path / "chromium"
    assert not TRUST.synchronize(ipa_ca_dir, firefox, chromium, BASELINE, check=True)
    assert not firefox.exists()
    assert not TRUST.synchronize(ipa_ca_dir, firefox, chromium, BASELINE)
    policy = json.loads((firefox / "policies.json").read_text())
    installed = policy["policies"]["Certificates"]["Install"]
    assert {Path(path).read_bytes() for path in installed} == set(roots)
    chrome_policy = json.loads((chromium / "system-ca.json").read_text())
    assert set(chrome_policy["CACertificates"]) == {
        base64.b64encode(ssl.PEM_cert_to_DER_cert(root.decode())).decode()
        for root in roots
    }
    before = (firefox / "policies.json").stat().st_mtime_ns
    assert TRUST.synchronize(ipa_ca_dir, firefox, chromium, BASELINE)
    assert (firefox / "policies.json").stat().st_mtime_ns == before
    Path(installed[0]).unlink()
    assert not TRUST.synchronize(ipa_ca_dir, firefox, chromium, BASELINE, check=True)
    TRUST.synchronize(ipa_ca_dir, firefox, chromium, BASELINE)
    legacy = firefox / "certs/atlas-infrastructure.crt"
    legacy.write_bytes(roots[0])
    (ipa_ca_dir / "first.crt").unlink()
    (ipa_ca_dir / "second.crt").write_bytes(roots[1])
    TRUST.synchronize(ipa_ca_dir, firefox, chromium, BASELINE)
    assert not legacy.exists()
    assert len(list((firefox / "certs").glob("*.crt"))) == 1
    assert TRUST.synchronize(ipa_ca_dir, firefox, chromium, BASELINE, check=True)


@pytest.mark.parametrize("invalid", ["malformed", "leaf-only", "empty", "missing"])
def test_invalid_ipa_certificates_preserve_existing_browser_trust(
    tmp_path: Path, invalid: str
) -> None:
    """Reject invalid input before changing any browser policy or certificate."""
    ipa_ca_dir = tmp_path / "ipa-ca"
    ipa_ca_dir.mkdir()
    enrolled = ipa_ca_dir / "enrolled.crt"
    enrolled.write_bytes(certificate("Existing CA"))
    firefox, chromium = tmp_path / "firefox", tmp_path / "chromium"
    TRUST.synchronize(ipa_ca_dir, firefox, chromium, BASELINE)
    existing = {
        path: path.read_bytes()
        for directory in (firefox, chromium)
        for path in directory.rglob("*")
        if path.is_file()
    }
    enrolled.unlink()
    if invalid == "missing":
        ipa_ca_dir.rmdir()
    elif invalid != "empty":
        enrolled.write_bytes(
            certificate("Leaf", ca=False)
            if invalid == "leaf-only"
            else b"invalid certificate"
        )
    with pytest.raises((ssl.SSLError, ValueError)):
        TRUST.synchronize(ipa_ca_dir, firefox, chromium, BASELINE)
    for path, content in existing.items():
        assert path.read_bytes() == content


def test_baseline_changes_preserve_certificates_and_reject_invalid_json(
    tmp_path: Path,
) -> None:
    """Apply baseline changes without losing trust or replacing good data on errors."""
    ipa_ca_dir = tmp_path / "ipa-ca"
    ipa_ca_dir.mkdir()
    root = certificate("Existing CA")
    (ipa_ca_dir / "enrolled.crt").write_bytes(root)
    firefox, chromium = tmp_path / "firefox", tmp_path / "chromium"
    baseline = tmp_path / "baseline.json"
    baseline.write_bytes(BASELINE.read_bytes())
    TRUST.synchronize(ipa_ca_dir, firefox, chromium, baseline)
    policy_file = firefox / "policies.json"
    original = policy_file.read_bytes()
    policy = json.loads(original)
    assert policy["policies"]["DisableFormHistory"] is False
    assert policy["policies"]["PasswordManagerEnabled"] is False

    changed = json.loads(baseline.read_text())
    changed["policies"]["DisableFormHistory"] = True
    baseline.write_text(json.dumps(changed))
    assert not TRUST.synchronize(ipa_ca_dir, firefox, chromium, baseline, check=True)
    assert policy_file.read_bytes() == original
    assert not TRUST.synchronize(ipa_ca_dir, firefox, chromium, baseline)
    updated = json.loads(policy_file.read_text())
    assert updated["policies"]["DisableFormHistory"] is True
    assert updated["policies"]["Certificates"] == policy["policies"]["Certificates"]
    installed = updated["policies"]["Certificates"]["Install"]
    assert [Path(path).read_bytes() for path in installed] == [root]
    assert TRUST.synchronize(ipa_ca_dir, firefox, chromium, baseline, check=True)

    existing = {
        path: path.read_bytes()
        for directory in (firefox, chromium)
        for path in directory.rglob("*")
        if path.is_file()
    }
    baseline.write_text("invalid JSON")
    for check in (True, False):
        with pytest.raises(json.JSONDecodeError):
            TRUST.synchronize(ipa_ca_dir, firefox, chromium, baseline, check=check)
        for path, content in existing.items():
            assert path.read_bytes() == content


def test_browser_catalog_installs_update_hook_and_snap_policy_access(
    tmp_path: Path,
) -> None:
    """Keep policy generation idempotent and expose policies inside Chromium Snap."""
    catalog = resources(
        compile_catalog(
            tmp_path,
            "include freeipa_firefox\ninclude profile::desktop_snaps",
            {},
            hostname="aperture",
            node_type="desktop",
        )
    )
    hook = catalog["File[/etc/ca-certificates/update.d/browser-trust]"]
    assert hook["mode"] == "0755"
    assert hook["source"] == "puppet:///modules/freeipa_firefox/browser-trust.py"
    command = catalog["Exec[generate-browser-policies]"]
    assert command["unless"] == command["command"] + " --check"
    baseline = catalog["File[/etc/firefox/policies/baseline.json]"]
    assert (
        baseline["source"] == "puppet:///modules/freeipa_firefox/firefox-policies.json"
    )
    assert "File[/etc/firefox/policies/baseline.json]" in command["require"]
    assert "Exec[generate-firefox-policies]" not in catalog
    template = catalog["File[/etc/firefox/policies/user.js]"]
    assert template["source"] == "puppet:///modules/freeipa_firefox/firefox-user.js"
    profile_sync = catalog["Exec[sync-firefox-profile-preferences]"]
    assert profile_sync["command"] == "/usr/local/bin/firefox-profile-prefs"
    assert profile_sync["unless"] == profile_sync["command"] + " --check"
    assert profile_sync["user"] == "user"
    assert profile_sync["environment"] == ["HOME=/home/user"]
    assert "File[/etc/firefox/policies/user.js]" in profile_sync["require"]
    access = catalog["Exec[chromium-system-policies]"]
    assert access["command"] == (
        "/usr/bin/snap connect chromium:etc-chromium-browser-policies :system-files"
    )
    assert access["require"] == "Package[chromium]"
