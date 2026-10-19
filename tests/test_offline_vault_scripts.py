"""Check explicit offline-root dispatch and certificate import boundaries."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest


REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "docker/vault/scripts"


@pytest.mark.parametrize(
    "script,message",
    [
        ("01-pki-core-setup.sh", "refuses the legacy online-root"),
        ("03-puppet-external-ca.sh", "infra-pki.rb prepare"),
        ("04-sign-csr.sh", "rejects FreeIPA CA-full"),
    ],
)
def test_offline_dispatch_never_sources_legacy_configuration(
    tmp_path: Path, script: str, message: str
) -> None:
    """Do not read .env, use Vault, delete SSL state or invoke host services."""
    (tmp_path / ".env").write_text("touch legacy-configuration-was-read\n")
    result = subprocess.run(
        ["bash", str(SCRIPTS / script), "--offline-root"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 2
    assert message in result.stderr
    assert not (tmp_path / "legacy-configuration-was-read").exists()


def test_intermediate_dispatch_resolves_helper_relative_to_script(
    tmp_path: Path,
) -> None:
    """The opt-in path works outside /scripts without reading legacy config."""
    ruby = shutil.which("ruby")
    if ruby is None:
        pytest.skip("Ruby is required")
    result = subprocess.run(
        ["sh", str(SCRIPTS / "02-pki-intermediate.sh"), "--offline-root", "import"],
        cwd=tmp_path,
        env={"PATH": os.environ["PATH"], "OPENVOX_RUBY": ruby},
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode != 0
    assert "usage:" in result.stderr
    assert "/scripts/config.sh" not in result.stderr


@pytest.mark.parametrize(
    "address,skip_verify,message",
    [
        ("http://localhost:8200", "", "verified HTTPS"),
        ("https://localhost:8200", "true", "VAULT_SKIP_VERIFY is forbidden"),
    ],
)
def test_unsafe_admin_transport_fails_before_any_api_call(
    tmp_path: Path, address: str, skip_verify: str, message: str
) -> None:
    """Never use plaintext transport or an administrator's skip-verify setting."""
    ruby = shutil.which("ruby")
    if ruby is None:
        pytest.skip("Ruby is required")
    ca = tmp_path / "root.pem"
    ca.write_text("not used before validation\n")
    policy = tmp_path / "openssl.cnf"
    policy.write_text("")
    result = subprocess.run(
        [
            ruby,
            str(SCRIPTS / "offline-intermediate.rb"),
            "import",
            str(tmp_path),
            "0" * 64,
        ],
        env={
            "PATH": os.environ["PATH"],
            "VAULT_ADDR": address,
            "VAULT_TOKEN": "test-only-admin",
            "VAULT_CACERT": str(ca),
            "VAULT_CRL": str(ca),
            "OPENSSL_CONF": str(policy),
            "VAULT_SKIP_VERIFY": skip_verify,
        },
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode != 0
    assert message in result.stderr
    assert "test-only-admin" not in result.stderr


def test_wrong_root_pin_refused_before_import(tmp_path: Path) -> None:
    """An externally supplied chain cannot establish its own trust root."""
    ruby = shutil.which("ruby")
    if ruby is None:
        pytest.skip("Ruby is required")
    result = subprocess.run(
        [
            ruby,
            "-e",
            r"""
require 'openssl'
load ARGV.shift
directory = ARGV.shift
key = OpenSSL::PKey::RSA.new(2048)
root = OpenSSL::X509::Certificate.new
root.version = 2
root.serial = 1
root.subject = root.issuer = OpenSSL::X509::Name.parse('/CN=Untrusted root')
root.public_key = key
root.not_before = Time.now - 60
root.not_after = Time.now + 7200
root.sign(key, OpenSSL::Digest::SHA256.new)
File.write("#{directory}/root.pem", root.to_pem)
validate_chain(directory, '0' * 64)
""",
            str(SCRIPTS / "offline-intermediate.rb"),
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "root certificate does not match operator pin" in result.stderr
