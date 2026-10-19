"""Check FreeIPA SubjectPublicKeyInfo extraction for ML-DSA and classical keys."""

from pathlib import Path
import subprocess
import tempfile

from ipalib import x509


def check(algorithm: str) -> None:
    """Compare the library's DER SPKI with OpenSSL's independent decoding."""
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
                "/CN=SPKI fixture",
                "-days",
                "1",
                "-keyout",
                str(key),
                "-out",
                str(cert),
            ],
            capture_output=True,
            check=True,
        )
        public = subprocess.run(
            ["openssl", "x509", "-in", str(cert), "-pubkey", "-noout"],
            capture_output=True,
            check=True,
        ).stdout
        expected = subprocess.run(
            ["openssl", "pkey", "-pubin", "-outform", "DER"],
            input=public,
            capture_output=True,
            check=True,
        ).stdout
        certificate = x509.load_pem_x509_certificate(cert.read_bytes())
        assert certificate.public_key_info_bytes == expected
    print(f"{algorithm}: FreeIPA SPKI matches OpenSSL DER")


if __name__ == "__main__":
    for algorithm in ("ML-DSA-44", "rsa:2048", "EC"):
        check(algorithm)
