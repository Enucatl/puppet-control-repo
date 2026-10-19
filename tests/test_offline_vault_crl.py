"""Ensure privileged Vault requests enforce both CRLs before sending tokens."""

from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import shutil
import ssl
import subprocess
from threading import Thread

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import pytest


HELPER = Path(__file__).parents[1] / "docker/vault/scripts/offline-intermediate.rb"


@pytest.mark.parametrize(
    "state",
    [
        "valid",
        "revoked-leaf",
        "revoked-intermediate",
        "expired-issuer",
        "expired-root",
        "missing-issuer",
        "missing-root",
        "bad-signature",
        "empty",
        "malformed",
    ],
)
def test_admin_token_requires_current_unrevoked_chain(
    tmp_path: Path, state: str
) -> None:
    """A real TLS handshake must fail before an invalid peer receives a token."""
    ruby = shutil.which("ruby")
    if ruby is None:
        pytest.skip("Ruby is required")
    now = datetime.now(timezone.utc)
    keys = [
        rsa.generate_private_key(public_exponent=65537, key_size=2048) for _ in range(3)
    ]
    names = [
        x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        for name in ["Fixture root", "Fixture issuer", "localhost"]
    ]
    certificates = []
    for index in range(3):
        issuer = max(0, index - 1)
        builder = (
            x509.CertificateBuilder()
            .subject_name(names[index])
            .issuer_name(names[issuer])
            .public_key(keys[index].public_key())
            .serial_number(index + 1)
            .not_valid_before(now - timedelta(hours=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(
                x509.BasicConstraints(
                    ca=index < 2, path_length=1 - index if index < 2 else None
                ),
                critical=True,
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(keys[index].public_key()),
                False,
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(
                    keys[issuer].public_key()
                ),
                False,
            )
        )
        if index < 2:
            builder = builder.add_extension(
                x509.KeyUsage(
                    False, False, False, False, False, True, True, None, None
                ),
                True,
            )
        else:
            builder = builder.add_extension(
                x509.SubjectAlternativeName([x509.DNSName("localhost")]), False
            )
        certificates.append(builder.sign(keys[issuer], hashes.SHA256()))
    crls = []
    for index, label in enumerate(["root", "issuer"]):
        if state == f"missing-{label}":
            continue
        builder = (
            x509.CertificateRevocationListBuilder()
            .issuer_name(names[index])
            .last_update(now - timedelta(hours=1))
            .next_update(
                now + timedelta(minutes=-1 if state == f"expired-{label}" else 60)
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(
                    keys[index].public_key()
                ),
                False,
            )
        )
        if state == ("revoked-intermediate" if index == 0 else "revoked-leaf"):
            builder = builder.add_revoked_certificate(
                x509.RevokedCertificateBuilder()
                .serial_number(index + 2)
                .revocation_date(now - timedelta(minutes=1))
                .build()
            )
        signer = keys[2] if state == "bad-signature" else keys[index]
        crls.append(
            builder.sign(signer, hashes.SHA256()).public_bytes(
                serialization.Encoding.PEM
            )
        )
    ca, cert, key, crl = [
        tmp_path / name for name in ["root.pem", "server.pem", "server.key", "crls.pem"]
    ]
    ca.write_bytes(certificates[0].public_bytes(serialization.Encoding.PEM))
    cert.write_bytes(
        b"".join(
            item.public_bytes(serialization.Encoding.PEM)
            for item in reversed(certificates[1:])
        )
    )
    key.write_bytes(
        keys[2].private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    crl.write_bytes(
        b""
        if state == "empty"
        else b"".join(crls) + (b"unexpected input" if state == "malformed" else b"")
    )
    received = []

    class Handler(BaseHTTPRequestHandler):
        """Record requests that get through the TLS handshake."""

        def do_GET(self) -> None:
            """Respond only after recording the test administrator token."""
            received.append(self.headers.get("X-Vault-Token"))
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *_args) -> None:
            """Keep successful fixture requests silent."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = Thread(target=lambda: server.serve_forever(poll_interval=0.01))
    thread.start()
    try:
        result = subprocess.run(
            [ruby, "-e", "load ARGV.fetch(0); api('GET', 'sys/mounts')", str(HELPER)],
            env={
                "PATH": os.environ["PATH"],
                "VAULT_ADDR": f"https://localhost:{server.server_port}",
                "VAULT_TOKEN": "test-only-admin",
                "VAULT_CACERT": str(ca),
                "VAULT_CRL": str(crl),
            },
            capture_output=True,
            text=True,
            timeout=10,
        )
        if state == "valid":
            assert result.returncode == 0, result.stderr
            assert received == ["test-only-admin"]
        else:
            assert result.returncode != 0
            assert received == []
            assert "test-only-admin" not in result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
