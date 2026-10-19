#!/usr/bin/python3
"""Start the Atlas CA-less server using protected installer inputs."""

import json
import os
from pathlib import Path
import shlex
import socket


def installer_options(settings: dict[str, object]) -> list[str]:
    """Build the fixed CA-less configuration without Dogtag or PKINIT."""
    if (
        settings.get("hostname") != "ipa.home.arpa"
        or settings.get("domain") != "home.arpa"
        or settings.get("realm") != "HOME.ARPA"
        or settings.get("no_pkinit") is not True
    ):
        raise ValueError("Unexpected Atlas identity or PKINIT configuration")
    for field in ("admin_password", "dm_password"):
        if not isinstance(settings.get(field), str) or not settings[field]:
            raise ValueError("Missing protected installer password")
    return [
        "--unattended",
        "--hostname=ipa.home.arpa",
        "--domain=home.arpa",
        "--realm=HOME.ARPA",
        "--no-ntp",
        "--no-host-dns",
        "--no-pkinit",
        "--ca-cert-file=/bootstrap/root.pem",
        "--http-cert-file=/bootstrap/http-chain.pem",
        "--http-cert-file=/bootstrap/http-key.pem",
        "--http-pin=",
        "--dirsrv-cert-file=/bootstrap/ldap-chain.pem",
        "--dirsrv-cert-file=/bootstrap/ldap-key.pem",
        "--dirsrv-pin=",
        f"--admin-password={settings['admin_password']}",
        f"--ds-password={settings['dm_password']}",
    ]


def main() -> None:
    """Refuse unexpected state before handing control to the image's init."""
    os.umask(0o077)
    if socket.gethostname() != "ipa.home.arpa":
        raise RuntimeError("This image requires hostname ipa.home.arpa")
    data = Path("/data")
    if not (data / ".atlas-freeipa-volume").is_file():
        raise RuntimeError("Missing prepared Atlas identity volume")
    settings = json.loads(Path("/bootstrap/installer.json").read_text())
    identity = settings.get("volume_identity")
    if not identity or (data / ".atlas-freeipa-volume").read_text().strip() != identity:
        raise RuntimeError("Atlas identity volume does not match protected inputs")
    if (data / ".atlas-freeipa-installed").exists():
        if (
            not (data / "etc/ipa/default.conf").is_file()
            or (data / ".atlas-freeipa-installed").read_text().strip() != identity
        ):
            raise RuntimeError("Established FreeIPA state missing; restore it")
    else:
        options = installer_options(settings)
        path = data / "ipa-server-install-options"
        path.write_text("\n".join(shlex.quote(option) for option in options) + "\n")
        path.chmod(0o600)
    os.execv("/usr/local/sbin/init", ["/usr/local/sbin/init"])


if __name__ == "__main__":
    main()
