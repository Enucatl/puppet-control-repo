#!/usr/bin/env python3
"""Prepare certificates and password options for a fresh Atlas IPA installation."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shlex
import socket
import ssl
import stat

SPEC = importlib.util.spec_from_file_location(
    "atlas_infra", Path(__file__).with_name("atlas-infra.py")
)
assert SPEC and SPEC.loader
infra = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(infra)
STATE = infra.IPA_SECRETS / "bootstrap"
MOUNT = "pki_freeipa"
VOLUME = "infra_ipa_data"
HOSTNAME = "ipa.home.arpa"


def compose(*arguments: str) -> str:
    """Read the shared infrastructure configuration without ambient env files."""
    return infra.run(
        infra.DOCKER
        + [
            "compose",
            "--env-file",
            "/dev/null",
            "-p",
            "infra",
            "-f",
            str(infra.SOURCE / "compose.yaml"),
            *arguments,
        ]
    )


def intermediate() -> None:
    """Resume issuance only with the original Vault-held intermediate key."""
    request, signed, established = (
        STATE / name
        for name in ("intermediate.json", "intermediate.pem", "issuer.json")
    )
    mounts = infra.api("GET", "sys/mounts")["data"]
    if MOUNT + "/" not in mounts:
        if any(path.exists() for path in (request, signed, established)):
            raise RuntimeError("Established FreeIPA mount missing; restore it")
        infra.mount(MOUNT, "pki", {"config": {"max_lease_ttl": "43800h"}})
    keys = (
        infra.api("LIST", MOUNT + "/keys", accepted=(200, 404))
        .get("data", {})
        .get("keys", [])
    )
    issuers = (
        infra.api("LIST", MOUNT + "/issuers", accepted=(200, 404))
        .get("data", {})
        .get("keys", [])
    )
    if established.exists() and not issuers:
        raise RuntimeError("Established FreeIPA issuer missing; restore it")
    if not request.exists():
        if keys or issuers or signed.exists() or established.exists():
            raise RuntimeError("Missing saved FreeIPA CSR; restore it")
        infra.save(
            request,
            json.dumps(
                infra.api(
                    "POST",
                    MOUNT + "/intermediate/generate/internal",
                    {
                        "common_name": "Atlas FreeIPA intermediate",
                        "key_type": "ml-dsa",
                        "parameter_set": "ml-dsa-44",
                    },
                )["data"]
            ),
        )
        keys = infra.api("LIST", MOUNT + "/keys")["data"]["keys"]
    saved = json.loads(request.read_text())
    if saved["key_id"] not in keys:
        raise RuntimeError("Original FreeIPA intermediate key missing; restore it")
    if not signed.exists():
        if issuers or established.exists():
            raise RuntimeError("Missing saved FreeIPA certificate; restore it")
        infra.save(
            signed,
            infra.api(
                "POST",
                "pki/root/sign-intermediate",
                {
                    "csr": saved["csr"],
                    "format": "pem_bundle",
                    "ttl": "43800h",
                    "max_path_length": 0,
                },
            )["data"]["certificate"],
        )
    if not issuers:
        infra.api(
            "POST",
            MOUNT + "/intermediate/set-signed",
            {"certificate": signed.read_text()},
        )
    current = infra.api("GET", MOUNT + "/issuer/default")["data"]
    certificate = (
        signed.read_text().split("-----END CERTIFICATE-----", 1)[0]
        + "-----END CERTIFICATE-----\n"
    )
    if current["key_id"] != saved["key_id"] or ssl.PEM_cert_to_DER_cert(
        current["certificate"]
    ) != ssl.PEM_cert_to_DER_cert(certificate):
        raise RuntimeError(
            "FreeIPA intermediate certificate or key changed; restore it"
        )
    identity = {"key_id": current["key_id"], "issuer_id": current["issuer_id"]}
    if established.exists() and json.loads(established.read_text()) != identity:
        raise RuntimeError("FreeIPA issuer identity changed; restore it")
    infra.save(established, json.dumps(identity))


def initialize(password_file: Path | None = None) -> None:
    """Prepare fresh IPA credentials without changing existing root authority."""
    image = json.loads(compose("config", "--format", "json"))["services"]["ipa"][
        "image"
    ]
    infra.run(infra.DOCKER + ["volume", "create", VOLUME])
    # Native state must never be overwritten by another bootstrap.
    infra.run(
        infra.DOCKER
        + [
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--user",
            "0:0",
            "--mount",
            f"type=volume,src={VOLUME},dst=/data,readonly",
            "--entrypoint",
            "/bin/sh",
            image,
            "-ec",
            'test -z "$(ls -A /data)"',
        ]
    )
    root = infra.api("GET", "pki/cert/ca")["data"]["certificate"]
    if ssl.PEM_cert_to_DER_cert(root) != ssl.PEM_cert_to_DER_cert(infra.CA.read_text()):
        raise RuntimeError("Existing Atlas root certificate changed")
    intermediate()
    infra.api(
        "POST",
        MOUNT + "/roles/ipa-transport",
        {
            "key_type": "ml-dsa",
            "parameter_set": "ml-dsa-44",
            "allowed_domains": [HOSTNAME],
            "allow_bare_domains": True,
            "allow_subdomains": False,
            "allow_any_name": False,
            "allow_localhost": False,
            "allow_ip_sans": False,
            "allow_wildcard_certificates": False,
            "enforce_hostnames": True,
            "max_ttl": "2160h",
            "key_usage": ["DigitalSignature"],
            "server_flag": True,
            "client_flag": True,
        },
    )
    infra.IPA_SECRETS.mkdir(mode=0o700, parents=True, exist_ok=True)
    infra.IPA_SECRETS.chmod(0o700)
    chain = infra.IPA_SECRETS / "fullchain.pem"
    key = infra.IPA_SECRETS / "key.pem"
    if chain.exists() != key.exists():
        raise RuntimeError("Incomplete IPA certificate pair; restore the missing file")
    if not chain.exists():
        leaf = infra.api(
            "POST",
            MOUNT + "/issue/ipa-transport",
            {"common_name": HOSTNAME, "alt_names": HOSTNAME, "ttl": "2160h"},
        )["data"]
        infra.save(
            chain, leaf["certificate"] + "\n" + "\n".join(leaf["ca_chain"]) + "\n"
        )
        infra.save(key, leaf["private_key"])
    infra.save(infra.IPA_SECRETS / "ca.pem", root)
    installer = infra.IPA_SECRETS / "installer.json"
    if not installer.exists():
        if password_file:
            metadata = password_file.stat()
            if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) & 0o077:
                raise RuntimeError("Password file must belong to root with mode 0600")
            passwords = json.loads(password_file.read_text())
            if set(passwords) != {"admin_password", "dm_password"} or any(
                not isinstance(value, str) or len(value) < 12
                for value in passwords.values()
            ):
                raise RuntimeError(
                    "Password JSON needs admin_password and dm_password (12+ characters)"
                )
        else:
            passwords = {
                name: secrets.token_urlsafe(32)
                for name in ("admin_password", "dm_password")
            }
        infra.save(
            installer,
            json.dumps(
                {
                    "hostname": HOSTNAME,
                    "domain": "home.arpa",
                    "realm": "HOME.ARPA",
                    "no_pkinit": True,
                    **passwords,
                }
            ),
        )
    settings = json.loads(installer.read_text())
    infra.save(
        infra.IPA_SECRETS / "ipa-server-install-options",
        (
            "\n".join(
                shlex.quote(f"--{option}={settings[field]}")
                for option, field in (
                    ("admin-password", "admin_password"),
                    ("ds-password", "dm_password"),
                )
            )
            + "\n"
        ),
    )
    for path in (infra.IPA_SECRETS, *infra.IPA_SECRETS.iterdir()):
        os.chown(path, 100000, 100000)


def main() -> None:
    """Prepare fresh installation inputs on Atlas using its local Docker daemon."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("initialize",))
    parser.add_argument("--password-file", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    if os.geteuid() != 0 or socket.getfqdn() != "atlas.home.arpa":
        raise RuntimeError("Run as root on atlas.home.arpa")
    if (
        infra.run(infra.DOCKER + ["info", "--format", "{{.Name}}"]).strip()
        != socket.gethostname()
    ):
        raise RuntimeError("Refusing nonlocal Docker daemon")
    os.environ["OPENSSL_CONF"] = str(infra.SOURCE / "docker/openvox/openssl.cnf")
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    STATE.chmod(0o700)
    with infra.maintenance():
        initialize(args.password_file)
    print("Atlas IPA inputs prepared; start the server with Docker Compose")


if __name__ == "__main__":
    main()
