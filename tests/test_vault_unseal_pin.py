"""The sealed bootstrap authenticates its pinned key even after TLS leaf expiry."""

from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import ssl
import subprocess
import threading

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "docker/vault/scripts/unseal.sh"


@pytest.mark.parametrize("pin", ["matching", "wrong", "missing"])
def test_sealed_bootstrap_checks_local_key_before_transmitting_unseal_share(
    tmp_path, pin
):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "expired.test")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now - timedelta(days=2))
        .not_valid_after(now - timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert = tmp_path / "tls.crt"
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    private = tmp_path / "tls.key"
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public = tmp_path / "pin.pem"
    pin_key = (
        key
        if pin == "matching"
        else rsa.generate_private_key(public_exponent=65537, key_size=2048)
    )
    if pin != "missing":
        public.write_bytes(
            pin_key.public_key().public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            body = json.dumps({"initialized": True, "sealed": True}).encode()
            self.send_response(503)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            received.append(
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            )
            self.send_response(200)
            self.send_header("Content-Length", str(len(b'{"sealed":false}')))
            self.end_headers()
            self.wfile.write(b'{"sealed":false}')

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.load_cert_chain(cert, private)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    share = tmp_path / "unseal-key"
    share.write_text("protected-test-share\n")
    binary = tmp_path / "bin"
    binary.mkdir()
    sleep = binary / "sleep"
    sleep.write_text("#!/bin/sh\nexit 37\n")
    sleep.chmod(0o755)
    try:
        result = subprocess.run(
            ["bash", str(SCRIPT)],
            env={
                **os.environ,
                "PATH": str(binary) + ":" + os.environ["PATH"],
                "VAULT_ADDR": f"https://127.0.0.1:{server.server_port}",
                "VAULT_PINNED_PUBKEY": str(public),
                "UNSEAL_KEY_FILE": str(share),
            },
            capture_output=True,
            text=True,
            timeout=15,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert "protected-test-share" not in result.stdout + result.stderr
    if pin == "matching":
        assert result.returncode == 37
        assert received == [{"key": "protected-test-share"}]
        assert "Vault unsealed" in result.stdout
    else:
        assert result.returncode in (1, 37)
        assert received == []
