"""Exercise enrollment against a disposable Vault, never the configured server."""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import hvac
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID, ObjectIdentifier

ROOT = Path(__file__).resolve().parents[1]
CERTNAME = "enrollment.home.arpa"
SPEC = importlib.util.spec_from_file_location(
    "autosign_vault", ROOT / "scripts/autosign.py"
)
assert SPEC is not None and SPEC.loader is not None
autosign = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(autosign)


def make_csr(token: str, certname: str = CERTNAME) -> bytes:
    """Generate a signed CSR carrying an enrollment token."""
    return (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, certname)]))
        .add_attribute(ObjectIdentifier("1.2.840.113549.1.9.7"), token.encode())
        .sign(ec.generate_private_key(ec.SECP256R1()), hashes.SHA256())
        .public_bytes(serialization.Encoding.PEM)
    )


@pytest.fixture(scope="module")
def vault(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[hvac.Client, str]]:
    """Start a local Vault and an issuer with a deliberately powerful identity."""
    binary = shutil.which("vault")
    if binary is None:
        pytest.skip("Vault executable is required for disposable integration tests")
    directory = tmp_path_factory.mktemp("enrollment-vault")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    address = f"http://127.0.0.1:{port}"
    environment = {"PATH": os.environ["PATH"], "HOME": str(directory)}
    with (directory / "server.log").open("w") as log:
        process = subprocess.Popen(
            [
                binary,
                "server",
                "-dev",
                "-dev-root-token-id=disposable-enrollment-root",
                f"-dev-listen-address=127.0.0.1:{port}",
            ],
            env=environment,
            cwd=directory,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 15
            while True:
                try:
                    with urlopen(f"{address}/v1/sys/health", timeout=1) as response:
                        assert response.status == 200
                    break
                except URLError:
                    if process.poll() is not None or time.monotonic() >= deadline:
                        pytest.fail("Disposable Vault failed to start")
                    time.sleep(0.05)
            root = hvac.Client(url=address, token="disposable-enrollment-root")
            policy_script = (
                ROOT / "docker/vault/scripts/08-vault-puppet-policy.sh"
            ).read_text()
            policy = re.search(
                r"vault policy write puppet-enrollment - <<['\"]?EOF['\"]?\n(.*?)\nEOF",
                policy_script,
                re.DOTALL,
            )
            assert policy is not None, (
                "Enrollment policy must be installed by Vault setup"
            )
            root.sys.create_or_update_policy("puppet-enrollment", policy.group(1))
            root.sys.create_or_update_policy(
                "enrollment-issuer",
                'path "auth/token/create-orphan" { capabilities = ["update", "sudo"] }',
            )
            root.sys.create_or_update_policy(
                "administrator-identity",
                'path "*" { capabilities = ["create", "read", "update", "delete", "list", "sudo"] }',
            )
            root.sys.enable_auth_method("userpass")
            root.auth.userpass.create_or_update_user(
                "provisioner",
                password="disposable-password",
                policies=["enrollment-issuer"],
            )
            entity = root.secrets.identity.create_or_update_entity(
                name="provisioner", policies=["administrator-identity"]
            )
            root.secrets.identity.create_or_update_entity_alias(
                name="provisioner",
                canonical_id=entity["data"]["id"],
                mount_accessor=root.sys.list_auth_methods()["userpass/"]["accessor"],
            )
            issuer = hvac.Client(url=address)
            issuer.auth.userpass.login("provisioner", "disposable-password")
            assert issuer.auth.token.lookup_self()["data"]["identity_policies"] == [
                "administrator-identity"
            ]
            root.sys.enable_secrets_engine("kv", path="kv", options={"version": "2"})
            root.secrets.kv.v2.create_or_update_secret(
                mount_point="kv", path="puppet", secret={"value": "private"}
            )
            root.sys.enable_secrets_engine("pki", path="pki_int")
            root.secrets.pki.generate_root(
                "internal", common_name="home.arpa", mount_point="pki_int"
            )
            root.secrets.pki.create_or_update_role(
                "general",
                extra_params={
                    "allowed_domains": ["home.arpa"],
                    "allow_subdomains": True,
                    "ttl": "1h",
                },
                mount_point="pki_int",
            )
            # Confirm the inherited identity actually grants runtime access.
            assert issuer.secrets.kv.v2.read_secret_version(
                mount_point="kv", path="puppet", raise_on_deleted_version=True
            )["data"]["data"] == {"value": "private"}
            assert issuer.secrets.pki.generate_certificate(
                "general", common_name=CERTNAME, mount_point="pki_int"
            )["data"]["certificate"]
            yield root, issuer.token
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def enrollment_token(vault: tuple[hvac.Client, str], certname: str = CERTNAME) -> str:
    """Issue through the real provisioning helper without consuming the token."""
    root, issuer_token = vault
    result = subprocess.run(
        [
            "bash",
            "-c",
            'set -euo pipefail; source "$1"; create_vault_token "$2"; printf "%s" "$VM_TOKEN"',
            "enrollment-test",
            str(ROOT / "proxmox/lib.sh"),
            certname,
        ],
        env={
            "PATH": os.environ["PATH"],
            "VAULT_ADDR": root.url,
            "VAULT_TOKEN": issuer_token,
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, "Enrollment token helper failed"
    token = result.stdout
    assert token, "Enrollment token helper returned no token"
    info = root.auth.token.lookup(token)["data"]
    assert info["num_uses"] == 1
    assert info["renewable"] is False
    assert info["orphan"] is True
    assert info["type"] == "service"
    assert info["creation_ttl"] == 7200
    assert info["policies"] == ["puppet-enrollment"]
    assert not info.get("identity_policies")
    assert not info.get("entity_id")
    assert info["meta"] == {"certname": certname}
    return token


def approve(
    vault: tuple[hvac.Client, str], token: str, certname: str = CERTNAME
) -> None:
    """Run the actual autosign validation with a freshly signed CSR."""
    autosign.validate_csr(
        certname, make_csr(token, certname), vault[0].url, True, "home.arpa"
    )


def test_first_use_succeeds_and_replay_is_denied(
    vault: tuple[hvac.Client, str],
) -> None:
    """A successful lookup consumes the only permitted use."""
    token = enrollment_token(vault)
    approve(vault, token)
    with pytest.raises(hvac.exceptions.Forbidden):
        approve(vault, token)


def test_concurrent_attempts_approve_at_most_once(
    vault: tuple[hvac.Client, str],
) -> None:
    """Vault serializes the single-use limit across concurrent autosign requests."""
    token = enrollment_token(vault)
    barrier = threading.Barrier(8)

    def attempt() -> bool:
        """Start simultaneously and report whether autosigning succeeded."""
        barrier.wait(timeout=5)
        try:
            approve(vault, token)
            return True
        except hvac.exceptions.Forbidden, hvac.exceptions.InternalServerError:
            # Vault can return 500 while concurrent requests consume the token.
            # Autosign denies these requests too, so they are not approvals.
            return False

    with ThreadPoolExecutor(max_workers=8) as executor:
        approvals = list(executor.map(lambda _: attempt(), range(8)))
    assert sum(approvals) <= 1
    with pytest.raises(hvac.exceptions.Forbidden):
        approve(vault, token)


def test_expired_token_is_denied(vault: tuple[hvac.Client, str]) -> None:
    """Vault rejects an unused enrollment token after its TTL expires."""
    root, _ = vault
    token = root.auth.token.create_orphan(
        policies=["puppet-enrollment"],
        ttl="1s",
        renewable=False,
        no_default_policy=True,
        num_uses=1,
        meta={"certname": CERTNAME},
    )["auth"]["client_token"]
    time.sleep(1.2)
    with pytest.raises(hvac.exceptions.Forbidden):
        approve(vault, token)


def test_metadata_mismatch_consumes_token(vault: tuple[hvac.Client, str]) -> None:
    """A failed metadata comparison happens after Vault consumes the token."""
    token = enrollment_token(vault)
    with pytest.raises(PermissionError):
        approve(vault, token, "different.home.arpa")
    with pytest.raises(hvac.exceptions.Forbidden):
        approve(vault, token)


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "kv/data/puppet", None),
        ("post", "pki_int/issue/general", {"common_name": CERTNAME}),
        ("post", "auth/token/renew-self", {}),
        ("post", "auth/token/create", {}),
        ("post", "auth/token/create-orphan", {}),
    ],
)
def test_enrollment_has_no_runtime_or_admin_access(
    vault: tuple[hvac.Client, str], method: str, path: str, body: dict | None
) -> None:
    """Even a powerful issuer's identity must not grant enrollment extra access."""
    root, _ = vault
    token = enrollment_token(vault)
    client = hvac.Client(url=root.url, token=token)
    with pytest.raises(hvac.exceptions.Forbidden):
        client.adapter.request(method, f"/v1/{path}", json=body)
