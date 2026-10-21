#!/usr/bin/env python3
"""Prepare an explicit CA-less Atlas IPA installation or start its saved state."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import stat
import uuid

SPEC = importlib.util.spec_from_file_location(
    "atlas_infra", Path(__file__).with_name("atlas-infra.py")
)
assert SPEC and SPEC.loader
infra = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(infra)
STATE = infra.STATE / "freeipa"
MOUNT = "pki_freeipa"
VOLUME = "infra_ipa_data"
HOSTNAME = "ipa.home.arpa"
INPUTS = (
    "installer.json",
    "root.pem",
    "http-chain.pem",
    "ldap-chain.pem",
    "http-key.pem",
    "ldap-key.pem",
)


def compose(*arguments: str) -> str:
    """Use only the dedicated IPA deployment and no ambient secrets."""
    return infra.run(
        infra.DOCKER
        + [
            "compose",
            "--env-file",
            "/dev/null",
            "-p",
            "infra",
            "-f",
            str(infra.SOURCE / "docker/freeipa/atlas-compose.yaml"),
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


def volume(create: bool = False) -> None:
    """Pin the authority label and identity inside the persistent IPA data."""
    checkpoint = STATE / "volume.json"
    existing = infra.run(
        infra.DOCKER + ["volume", "ls", "--format", "{{.Name}}"]
    ).splitlines()
    if VOLUME not in existing:
        if checkpoint.exists() or not create:
            raise RuntimeError("Established IPA volume missing; restore it")
        infra.run(infra.DOCKER + ["volume", "create", "--label", infra.LABEL, VOLUME])
    metadata = json.loads(infra.run(infra.DOCKER + ["volume", "inspect", VOLUME]))[0]
    if metadata.get("Labels", {}).get("home.arpa.authority") != "atlas":
        raise RuntimeError("Refusing unmanaged IPA volume")
    saved = json.loads(checkpoint.read_text()) if checkpoint.exists() else None
    command = infra.DOCKER + [
        "run",
        "--rm",
        "-i",
        "--network",
        "none",
        "--user",
        "0:0",
        "--mount",
        f"type=volume,src={VOLUME},dst=/volume",
        "--entrypoint",
        "/bin/sh",
        "infra-openvox:24001",
        "-c",
    ]
    if saved:
        marker = infra.run(command + ["cat /volume/.atlas-freeipa-volume"]).strip()
        if marker != saved["identity"]:
            raise RuntimeError("IPA volume identity mismatch; restore original data")
    elif create:
        identity = str(uuid.uuid4())
        # An interrupted first allocation must not adopt populated IPA storage.
        infra.run(
            command
            + [
                'set -eu; test -z "$(ls -A /volume)"; chown 0:0 /volume; chmod 0700 /volume; '
                "umask 077; cat > /volume/.atlas-freeipa-volume",
            ],
            identity + "\n",
        )
        infra.save(
            checkpoint,
            json.dumps({"identity": identity}),
        )
    else:
        raise RuntimeError("Missing saved IPA volume identity")


def initialize(password_file: Path | None = None) -> None:
    """Prepare fresh IPA credentials without changing existing root authority."""
    if (STATE / "ready.json").exists():
        raise RuntimeError("IPA already prepared; use start or recovery")
    if not (infra.STATE / "ready.json").is_file():
        raise RuntimeError("Initialize Atlas Vault/OpenVox first")
    root = infra.api("GET", "pki/cert/ca")["data"]["certificate"]
    fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(root)).hexdigest()
    if fingerprint != (infra.STATE / "root.sha256").read_text().strip():
        raise RuntimeError("Existing Atlas root fingerprint changed")
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
    for service in ("http", "ldap"):
        response = STATE / f"{service}.json"
        if not response.exists():
            infra.save(
                response,
                json.dumps(
                    infra.api(
                        "POST",
                        MOUNT + "/issue/ipa-transport",
                        {
                            "common_name": HOSTNAME,
                            "alt_names": HOSTNAME,
                            "ttl": "2160h",
                        },
                    )["data"]
                ),
            )
        leaf = json.loads(response.read_text())
        infra.save(
            STATE / f"{service}-chain.pem",
            leaf["certificate"] + "\n" + "\n".join(leaf["ca_chain"]) + "\n",
        )
        infra.save(STATE / f"{service}-key.pem", leaf["private_key"])
    infra.save(STATE / "root.pem", root)
    installer = STATE / "installer.json"
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
    volume(create=True)
    settings = json.loads(installer.read_text())
    settings["volume_identity"] = json.loads((STATE / "volume.json").read_text())[
        "identity"
    ]
    infra.save(installer, json.dumps(settings))
    staged = STATE / "container-inputs"
    staged.mkdir(mode=0o700, exist_ok=True)
    for name in INPUTS:
        infra.save(staged / name, (STATE / name).read_text())
        os.chown(staged / name, 100000, 100000)
    os.chown(staged, 100000, 100000)
    infra.save(
        STATE / "ready.json",
        json.dumps({"hostname": HOSTNAME, "root_sha256": fingerprint}),
    )


def start() -> None:
    """Start only prepared IPA state, without issuing or replacing credentials."""
    if not (STATE / "ready.json").is_file():
        raise RuntimeError(
            "Missing initialized IPA state; explicitly initialize or restore it"
        )
    ready = json.loads((STATE / "ready.json").read_text())
    if (
        ready["hostname"] != HOSTNAME
        or ready["root_sha256"] != (infra.STATE / "root.sha256").read_text().strip()
    ):
        raise RuntimeError("Established IPA identity changed")
    for name in (
        "installer.json",
        "issuer.json",
        "intermediate.json",
        "intermediate.pem",
        "root.pem",
        "http.json",
        "ldap.json",
        "http-chain.pem",
        "ldap-chain.pem",
        "http-key.pem",
        "ldap-key.pem",
    ):
        if not (STATE / name).is_file():
            raise RuntimeError(f"Missing established IPA file: {name}")
    for name in INPUTS:
        staged = STATE / "container-inputs" / name
        if not staged.is_file() or staged.read_bytes() != (STATE / name).read_bytes():
            raise RuntimeError(f"Missing or changed staged IPA input: {name}")
    volume()
    if (STATE / "installed").exists():
        compose(
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "--entrypoint",
            "/bin/sh",
            "ipa",
            "-c",
            "test -s /data/.atlas-freeipa-installed",
        )
    compose("up", "-d", "--no-deps", "--wait", "--wait-timeout", "600", "ipa")
    compose(
        "exec",
        "-T",
        "ipa",
        "/bin/sh",
        "-c",
        "umask 077; cp /data/.atlas-freeipa-volume /data/.atlas-freeipa-installed",
    )
    infra.save(STATE / "installed", "healthy CA-less IPA\n")


def main() -> None:
    """Require the promoted Atlas host and its local Docker daemon."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("initialize", "start"))
    parser.add_argument("--password-file", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    if os.geteuid() != 0 or socket.getfqdn() != "atlas.home.arpa":
        raise RuntimeError("Run as root on atlas.home.arpa")
    if (
        Path("/etc/openvox-infra/promoted").read_text().strip()
        != "openvox-authority:atlas.home.arpa"
    ):
        raise RuntimeError("Promote Atlas before creating authoritative state")
    if (
        infra.run(infra.DOCKER + ["info", "--format", "{{.Name}}"]).strip()
        != socket.gethostname()
    ):
        raise RuntimeError("Refusing nonlocal Docker daemon")
    if infra.git("status", "--porcelain").strip():
        raise RuntimeError("Activation requires a clean versioned checkout")
    os.environ["OPENSSL_CONF"] = str(infra.SOURCE / "docker/openvox/openssl.cnf")
    STATE.mkdir(mode=0o700, exist_ok=True)
    STATE.chmod(0o700)
    with infra.maintenance():
        if args.action == "initialize":
            initialize(args.password_file)
        elif args.password_file:
            raise RuntimeError("--password-file is only valid for initialize")
        start()
    print("Atlas CA-less IPA started; account migration is a separate operation")


if __name__ == "__main__":
    main()
