"""Check Negotiate with ML-DSA and classical certificates and rejected TLS trust."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import ssl
import subprocess
import tempfile
import threading


class Challenge(BaseHTTPRequestHandler):
    """Challenge a credential-free request without issuing a Kerberos identity."""

    def handle(self) -> None:
        """Allow clients to close the connection after rejecting the certificate."""
        try:
            super().handle()
        except ConnectionResetError, BrokenPipeError:
            pass

    def do_HEAD(self) -> None:
        """Trigger libcurl's TLS channel binding calculation for Negotiate."""
        self.send_response(401)
        self.send_header("WWW-Authenticate", "Negotiate")
        self.end_headers()

    def log_message(self, *_args: object) -> None:
        """Keep test output limited to its results."""


def check(algorithm: str) -> None:
    """Exercise channel binding and retain CA and hostname verification."""
    with tempfile.TemporaryDirectory() as directory:
        cert = Path(directory) / "cert.pem"
        key = Path(directory) / "key.pem"
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                algorithm,
                *(
                    ["-pkeyopt", "ec_paramgen_curve:prime256v1"]
                    if algorithm == "EC"
                    else []
                ),
                "-noenc",
                "-subj",
                "/CN=localhost",
                "-addext",
                "subjectAltName=DNS:localhost",
                "-days",
                "1",
                "-keyout",
                str(key),
                "-out",
                str(cert),
            ],
            check=True,
            capture_output=True,
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), Challenge)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_port
            command = [
                "/usr/bin/curl",
                "--noproxy",
                "*",
                "--negotiate",
                "-u",
                ":",
                "--silent",
                "--show-error",
                "--head",
                "--output",
                "/dev/null",
                "--write-out",
                "%{http_code}",
                "--max-time",
                "10",
            ]
            result = subprocess.run(
                command + ["--cacert", str(cert), f"https://localhost:{port}/"],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, result.stderr
            assert result.stdout == "401", result.stdout
            for arguments in (
                [f"https://localhost:{port}/"],
                ["--cacert", str(cert), f"https://127.0.0.1:{port}/"],
            ):
                result = subprocess.run(command + arguments, capture_output=True)
                assert result.returncode == 60, (algorithm, result.stderr)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
    print(f"{algorithm}: Negotiate accepts trusted TLS and rejects wrong CA/hostname")


if __name__ == "__main__":
    for algorithm in ("ML-DSA-44", "rsa:2048", "EC"):
        check(algorithm)
