#!/usr/bin/env python3
"""Renew established Vault and CA-less IPA leaves without authority credentials."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import socket
import time

SPEC = importlib.util.spec_from_file_location(
    "atlas_infra", Path(__file__).with_name("atlas-infra.py")
)
assert SPEC and SPEC.loader
infra = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(infra)
SSL = Path("/etc/puppetlabs/puppet/ssl")


def configure() -> None:
    """Authorize Atlas's existing agent identity for these exact signing roles."""
    infra.api(
        "PUT",
        "sys/policies/acl/atlas-transport-renew",
        {
            "policy": "\n".join(
                f'path "{path}" {{ capabilities = ["update"] }}'
                for path in (
                    "pki_int/sign/vault-transport",
                    "pki_freeipa/sign/ipa-transport",
                    "auth/token/revoke-self",
                )
            )
        },
    )
    infra.api(
        "POST",
        "auth/cert/certs/atlas-transport-renew",
        {
            "certificate": infra.openvox("/bin/cat", "/trust/openvox-chain.pem"),
            "allowed_common_names": ["atlas.home.arpa"],
            "allowed_dns_sans": ["atlas.home.arpa"],
            "token_policies": ["atlas-transport-renew"],
            "token_no_default_policy": True,
            "token_ttl": "5m",
            "token_max_ttl": "5m",
        },
    )


def api(path: str, body: dict, token: str | None = None) -> dict:
    """Use short-lived cert-auth tokens in memory, never the saved root token."""
    request = {
        "method": "POST",
        "path": path,
        "body": body,
        "trust": str(infra.STATE / "root.pem"),
        "auth": token is not None,
        "token": token,
        "cert_file": str(SSL / "certs/atlas.home.arpa.pem"),
        "key_file": str(SSL / "private_keys/atlas.home.arpa.pem"),
    }
    response = json.loads(
        infra.run(
            [infra.RUBY, str(infra.SOURCE / "docker/openvox/infra-vault-api.rb")],
            json.dumps(request),
        )
    )
    if response["status"] not in (200, 204):
        raise RuntimeError(f"Transport renewal {path}: HTTP {response['status']}")
    return response["body"]


def certificate(
    data: dict, service: str, previous: dict | None = None, *, live: bool = False
) -> dict:
    """Validate saved identity and chain and optionally its active TLS listener."""
    request = {
        "data": data,
        "previous": previous or data,
        "hostname": "vault.home.arpa" if service == "vault" else "ipa.home.arpa",
        "root": str(infra.STATE / "root.pem"),
        "root_sha256": (infra.STATE / "root.sha256").read_text().strip(),
        "window_days": 10 if service == "vault" else 30,
        "inspect_only": previous is None and not live,
    }
    if live:
        request["port"] = {"vault": 8200, "http": 443, "ldap": 636}[service]
    return json.loads(
        infra.run(
            [infra.RUBY, str(infra.SOURCE / "docker/openvox/infra-transport-cert.rb")],
            json.dumps(request),
        )
    )


def install(data: dict, service: str, public_key: str) -> None:
    """Publish recovery inputs before applying and reloading the same saved leaf."""
    chain = data["certificate"] + "\n" + "\n".join(data["ca_chain"]) + "\n"
    if service == "vault":
        infra.save(infra.STATE / "vault-transport.json", json.dumps(data))
        infra.volume_file("certificates", "vault.key", data["private_key"])
        infra.volume_file("certificates", "public/vault.pub.pem", public_key)
        infra.volume_file("certificates", "vault_chain.crt", chain)
        infra.run(infra.DOCKER + ["kill", "--signal", "HUP", "infra-vault-1"])
        return
    state = infra.STATE / "freeipa"
    infra.save(state / f"{service}.json", json.dumps(data))
    for name, contents in (
        (f"{service}-chain.pem", chain),
        (f"{service}-key.pem", data["private_key"]),
    ):
        infra.save(state / name, contents)
        staged = state / "container-inputs" / name
        if staged.parent.is_dir():
            infra.save(staged, contents)
            os.chown(staged, 100000, 100000)
    # Keep credentials on stdin; native startup needs no host credential mount.
    worker = """import json, os, sys, tempfile
from pathlib import Path
from ipaserver.install.ipa_server_certinstall import ServerCertInstall
os.umask(0o077)
request = json.load(sys.stdin)
ServerCertInstall.make_parser()
with tempfile.TemporaryDirectory(prefix="ipa-certinstall-") as directory:
    chain = Path(directory) / "chain.pem"
    key = Path(directory) / "key.pem"
    chain.write_text(request["chain"])
    key.write_text(request["private_key"])
    options, args = ServerCertInstall.option_parser.parse_args([
        "--http" if request["service"] == "http" else "--dirsrv", "--pin=",
        str(chain), str(key)])
    options.dirman_password = request["dm_password"]
    result = ServerCertInstall(options, args).execute()
raise SystemExit(result or 0)
"""
    infra.run(
        infra.DOCKER + ["exec", "-i", "infra-ipa-1", "python3", "-c", worker],
        json.dumps(
            {
                "service": service,
                "chain": chain,
                "private_key": data["private_key"],
                "dm_password": json.loads((state / "installer.json").read_text())[
                    "dm_password"
                ],
            }
        ),
    )
    unit = "httpd.service" if service == "http" else "dirsrv@HOME-ARPA.service"
    infra.run(infra.DOCKER + ["exec", "infra-ipa-1", "systemctl", "restart", unit])


def renew(service: str) -> None:
    """Retry a persisted issuance after any interrupted install or listener reload."""
    path = (
        infra.STATE / "vault-transport.json"
        if service == "vault"
        else infra.STATE / "freeipa" / f"{service}.json"
    )
    current = json.loads(path.read_text())
    pending = path.with_suffix(".renewal-pending.json")
    if not pending.exists():
        info = certificate(current, service)
        if not info["due"]:
            certificate(current, service, live=True)
            print(f"{service}: verified; expires {info['not_after']}")
            return
        token = api("auth/cert/login", {"name": "atlas-transport-renew"})["auth"][
            "client_token"
        ]
        try:
            mount, role, ttl = (
                ("pki_int", "vault-transport", "720h")
                if service == "vault"
                else ("pki_freeipa", "ipa-transport", "2160h")
            )
            issued = api(
                f"{mount}/sign/{role}",
                {"csr": info["csr"], "ttl": ttl},
                token,
            )["data"]
        finally:
            api("auth/token/revoke-self", {}, token)
        data = {**issued, "private_key": current["private_key"]}
        validated = certificate(data, service, current)
        if validated["due"]:
            raise RuntimeError(
                "Renewal did not extend the certificate beyond its renewal window"
            )
        infra.save(pending, json.dumps(data))
    data = json.loads(pending.read_text())
    info = certificate(data, service, current)
    install(data, service, info["public_key"])
    deadline = time.monotonic() + 60
    while True:
        try:
            certificate(data, service, live=True)
            break
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(2)
    pending.unlink()
    descriptor = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    print(f"{service}: renewed and verified; expires {info['not_after']}")


def main() -> None:
    """Operate only on established local Atlas authority state."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("configure", "renew"))
    args = parser.parse_args()
    os.umask(0o077)
    if os.geteuid() != 0 or socket.getfqdn() != "atlas.home.arpa":
        raise RuntimeError("Run as root on atlas.home.arpa")
    if (
        Path("/etc/openvox-infra/promoted").read_text().strip()
        != "openvox-authority:atlas.home.arpa"
        or not (infra.STATE / "ready.json").is_file()
    ):
        raise RuntimeError("Renewal requires established promoted Atlas state")
    os.environ["OPENSSL_CONF"] = str(infra.SOURCE / "docker/openvox/openssl.cnf")
    with infra.maintenance():
        if args.action == "configure":
            configure()
        else:
            for service in ("vault", "http", "ldap"):
                renew(service)


if __name__ == "__main__":
    main()
