"""Exercise enrollment revocation verification against a real HTTPS peer."""

from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import ssl
from threading import Thread

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import pytest
import requests


SPEC = importlib.util.spec_from_file_location(
    "autosign", Path(__file__).parents[1] / "scripts/autosign.py"
)
autosign = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(autosign)


@pytest.mark.parametrize("state", ("valid", "revoked", "expired", "missing"))
def test_enrollment_requires_current_unrevoked_vault_chain(
    tmp_path: Path, state: str
) -> None:
    """Reject invalid CRL state before sending the single-use token to Vault."""
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Disposable CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(1)
        .not_valid_before(now - timedelta(hours=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, None, None),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), False
        )
        .sign(ca_key, hashes.SHA256())
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(2)
        .not_valid_before(now - timedelta(hours=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    crl = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(ca_name)
        .last_update(now - timedelta(hours=1))
        .next_update(now + timedelta(minutes=-1 if state == "expired" else 60))
    )
    if state == "revoked":
        crl = crl.add_revoked_certificate(
            x509.RevokedCertificateBuilder()
            .serial_number(2)
            .revocation_date(now - timedelta(minutes=1))
            .build()
        )
    ca_path, cert_path, key_path, crl_path = (
        tmp_path / name for name in ("ca.pem", "server.pem", "server.key", "crl.pem")
    )
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    cert_path.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    crl_path.write_bytes(
        ca.public_bytes(serialization.Encoding.PEM)
        if state == "missing"
        else crl.sign(ca_key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM)
    )
    requests_received = []

    class Handler(BaseHTTPRequestHandler):
        """Serve the minimal Vault enrollment response over verified TLS."""

        def do_GET(self) -> None:
            """Return bound metadata after TLS verification succeeds."""
            requests_received.append(self.path)
            body = json.dumps(
                {
                    "data": {
                        "policies": ["puppet-enrollment"],
                        "meta": {"certname": "test.home.arpa"},
                    }
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args) -> None:
            """Keep the test server silent."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = Thread(target=lambda: server.serve_forever(poll_interval=0.01))
    thread.start()
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(
            x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test.home.arpa")])
        )
        .add_attribute(x509.ObjectIdentifier("1.2.840.113549.1.9.7"), b"fixture-token")
        .sign(key, hashes.SHA256())
        .public_bytes(serialization.Encoding.PEM)
    )
    try:
        arguments = (
            "test.home.arpa",
            csr,
            f"https://localhost:{server.server_port}",
            str(ca_path),
            "home.arpa",
            str(crl_path),
        )
        if state == "valid":
            autosign.validate_csr(*arguments)
            assert requests_received == ["/v1/auth/token/lookup-self"]
        else:
            with pytest.raises(requests.exceptions.SSLError):
                autosign.validate_csr(*arguments)
            assert requests_received == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
