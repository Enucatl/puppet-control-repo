#!/usr/bin/python3
"""Add FreeIPA-enrolled CAs to Linux Firefox and Chromium policies."""

import base64
import hashlib
import json
from pathlib import Path
import ssl
import sys


def synchronize(
    ipa_ca_dir: Path,
    firefox: Path,
    chromium: Path,
    baseline: Path,
    *,
    check: bool = False,
) -> bool:
    """Check or update browser policies without touching browser profiles."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    for certificate_file in sorted(ipa_ca_dir.glob("*.crt")):
        context.load_verify_locations(cafile=str(certificate_file))
    certificates = {
        hashlib.sha256(der).hexdigest(): der
        for der in context.get_ca_certs(binary_form=True)
    }
    if not certificates:
        raise ValueError("FreeIPA certificate directory contains no CA certificates")

    expected = {
        firefox / "certs" / f"{digest}.crt": ssl.DER_cert_to_PEM_cert(der)
        for digest, der in certificates.items()
    }
    # shortcut: Firefox retains imported profile CAs; use a trust module if removals must propagate.
    firefox_policy = json.loads(baseline.read_text())
    firefox_policy["policies"]["Certificates"] = {
        "Install": [str(path) for path in expected]
    }
    chromium_policy = {
        "CACertificates": [
            base64.b64encode(der).decode() for der in certificates.values()
        ]
    }
    expected[firefox / "policies.json"] = json.dumps(firefox_policy, indent=2) + "\n"
    expected[chromium / "system-ca.json"] = json.dumps(chromium_policy) + "\n"

    obsolete = set((firefox / "certs").glob("*.crt")) - expected.keys()
    changed = bool(obsolete)
    for path, content in expected.items():
        if path.exists() and path.read_text() == content:
            continue
        changed = True
        if not check:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(content)
            temporary.chmod(0o644)
            temporary.replace(path)
    if not check:
        for path in obsolete:
            path.unlink()
    return not changed


if __name__ == "__main__":
    check = sys.argv[1:] == ["--check"]
    current = synchronize(
        Path("/usr/local/share/ca-certificates/ipa-ca"),
        Path("/etc/firefox/policies"),
        Path("/etc/chromium-browser/policies/managed"),
        Path("/etc/firefox/policies/baseline.json"),
        check=check,
    )
    sys.exit(int(check and not current))
