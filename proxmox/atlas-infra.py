#!/usr/bin/env python3
"""Explicitly initialize fresh Atlas Vault and OpenVox authorities."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import time
from collections.abc import Iterator

SOURCE = Path(__file__).resolve().parents[1]
VAULT_SECRETS = SOURCE / "docker/vault/secrets"
IPA_SECRETS = SOURCE / "docker/freeipa/secrets"
STATE = VAULT_SECRETS / "bootstrap"
CA = VAULT_SECRETS / "ca.pem"
RUBY = "/opt/puppetlabs/puppet/bin/ruby"
PUPPET = "/opt/puppetlabs/bin/puppet"
DOCKER = ["docker", "--host", "unix:///var/run/docker.sock"]
OPENVOX_VOLUMES = ("ssl", "ca", "code", "data", "gems", "git", "r10k_cache", "trust")
VAULT_VOLUMES = ("vault_data", "vault_logs", "vault_unseal")
MAINTENANCE_LOCK = Path("/run/lock/atlas-openvox-maintenance.lock")


@contextmanager
def maintenance() -> Iterator[None]:
    """Exclude publication while initializing authority state."""
    with MAINTENANCE_LOCK.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def run(arguments: list[str], data: str | None = None) -> str:
    """Keep command diagnostics private because API output can contain secrets."""
    result = subprocess.run(
        arguments,
        input=data,
        stdin=subprocess.DEVNULL if data is None else None,
        text=True,
        capture_output=True,
        timeout=1800,
    )
    if result.returncode:
        log = STATE / f"failure-{time.time_ns()}.log"
        log.write_text(result.stdout + result.stderr)
        log.chmod(0o600)
        raise RuntimeError(f"Command failed ({result.returncode}); private log: {log}")
    return result.stdout


def compose(*arguments: str, data: str | None = None) -> str:
    """Use the canonical production Compose configuration without local env files."""
    return run(
        DOCKER
        + [
            "compose",
            "--env-file",
            "/dev/null",
            "-p",
            "infra",
            "-f",
            str(SOURCE / "compose.yaml"),
            *arguments,
        ],
        data,
    )


def save(path: Path, contents: str) -> None:
    """Atomically persist protected credentials and initialization checkpoints."""
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(contents)
    temporary.chmod(0o600)
    with temporary.open("r") as stream:
        os.fsync(stream.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def api(
    method: str,
    path: str,
    body: dict | None = None,
    *,
    auth: bool = True,
    accepted: tuple[int, ...] = (200, 204),
) -> dict:
    """Use the accepted native Ruby/OpenSSL runtime for authenticated TLS."""
    result = json.loads(
        run(
            [RUBY, str(SOURCE / "docker/openvox/infra-vault-api.rb")],
            json.dumps(
                {
                    "method": method,
                    "path": path,
                    "body": body or {},
                    "auth": auth,
                    "trust": str(CA),
                    "token_file": str(VAULT_SECRETS / "root-token"),
                }
            ),
        )
    )
    if result["status"] not in accepted:
        raise RuntimeError(f"Vault {method} {path}: HTTP {result['status']}")
    return result["body"]


def health() -> dict:
    """Wait for the listener without treating sealed/uninitialized as failure."""
    deadline = time.monotonic() + 90
    while True:
        try:
            return api("GET", "sys/health", auth=False, accepted=(200, 501, 503))
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)


def volume_file(volume: str, path: str, contents: str) -> None:
    """Write through a remapped container identity, never expose keys in argv."""
    uid = "999:999" if volume.startswith("openvox_") else "100:100"
    run(
        DOCKER
        + [
            "run",
            "--rm",
            "-i",
            "--network",
            "none",
            "--user",
            uid,
            "--mount",
            f"type=volume,src=infra_{volume},dst=/volume",
            "--entrypoint",
            "/bin/sh",
            "infra-openvox:24001",
            "-c",
            'set -eu; umask 077; mkdir -p "$(dirname "$1")"; cat > "$1.tmp"; mv "$1.tmp" "$1"',
            "write",
            "/volume/" + path,
        ],
        contents,
    )


def openvox(*arguments: str) -> str:
    """Run explicit setup commands with the declared service mounts."""
    return compose(
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "--entrypoint",
        arguments[0],
        "openvox",
        *arguments[1:],
    )


def git(*arguments: str) -> str:
    """Use the checkout owner and its SSH configuration for publication inputs."""
    return run(["runuser", "-u", "user_l", "--", "git", "-C", str(SOURCE), *arguments])


def install_transport(root: str | None = None) -> None:
    """Prepare the temporary TLS listener before Vault can issue its own leaf."""
    bootstrap = STATE / "transport"
    VAULT_SECRETS.mkdir(mode=0o750, parents=True, exist_ok=True)
    os.chown(VAULT_SECRETS, 100100, 100100)
    for name, source in (("key.pem", "tls.key"), ("fullchain.pem", "tls.crt")):
        path = VAULT_SECRETS / name
        if not path.exists():
            save(path, (bootstrap / source).read_text())
            os.chown(path, 100100, 100100)
    if root is not None or not CA.exists():
        trust = (root + "\n" if root else "") + (bootstrap / "trust.pem").read_text()
        save(CA, trust)
        os.chown(CA, 100100, 100100)


def mount(name: str, kind: str, options: dict | None = None) -> None:
    """Create an absent mount without replacing existing issuer state."""
    if name + "/" not in api("GET", "sys/mounts")["data"]:
        api("POST", "sys/mounts/" + name, {"type": kind, **(options or {})})


def services_intermediate() -> None:
    """Import only the saved services certificate and its original Vault key."""
    request = STATE / "vault-intermediate.json"
    signed = STATE / "vault-intermediate.pem"
    established = STATE / "vault-intermediate-established.json"
    if (
        any(path.exists() for path in (request, signed, established))
        and "pki_int/" not in api("GET", "sys/mounts")["data"]
    ):
        raise RuntimeError("Established services mount missing; restore existing state")
    mount("pki_int", "pki", {"config": {"max_lease_ttl": "43800h"}})
    issuers = (
        api("LIST", "pki_int/issuers", accepted=(200, 404))
        .get("data", {})
        .get("keys", [])
    )
    keys = (
        api("LIST", "pki_int/keys", accepted=(200, 404)).get("data", {}).get("keys", [])
    )
    if established.exists() and not issuers:
        raise RuntimeError(
            "Established services issuer missing; restore existing state"
        )
    if not request.exists():
        if keys or issuers or signed.exists() or established.exists():
            raise RuntimeError("Missing saved intermediate CSR; recover request state")
        save(
            request,
            json.dumps(
                api(
                    "POST",
                    "pki_int/intermediate/generate/internal",
                    {
                        "common_name": "Atlas services intermediate",
                        "key_type": "ml-dsa",
                        "parameter_set": "ml-dsa-44",
                    },
                )["data"]
            ),
        )
        keys = api("LIST", "pki_int/keys")["data"]["keys"]
    saved_request = json.loads(request.read_text())
    if saved_request["key_id"] not in keys:
        raise RuntimeError(
            "Original services intermediate key missing; restore existing state"
        )
    if not signed.exists():
        if issuers or established.exists():
            raise RuntimeError(
                "Missing saved services certificate; restore existing state"
            )
        chain = api(
            "POST",
            "pki/root/sign-intermediate",
            {
                "csr": saved_request["csr"],
                "format": "pem_bundle",
                "ttl": "43800h",
                "max_path_length": 0,
            },
        )["data"]["certificate"]
        save(signed, chain)
    chain = signed.read_text()
    if not issuers:
        api("POST", "pki_int/intermediate/set-signed", {"certificate": chain})
    current = api("GET", "pki_int/issuer/default")["data"]
    certificate = (
        chain.split("-----END CERTIFICATE-----", 1)[0] + "-----END CERTIFICATE-----\n"
    )
    if (
        ssl.PEM_cert_to_DER_cert(current["certificate"])
        != ssl.PEM_cert_to_DER_cert(certificate)
        or current["key_id"] != saved_request["key_id"]
    ):
        raise RuntimeError(
            "Established services issuer or key changed; restore existing state"
        )
    identity = {"issuer_id": current["issuer_id"], "key_id": current["key_id"]}
    if established.exists() and json.loads(established.read_text()) != identity:
        raise RuntimeError(
            "Established services issuer identity changed; restore existing state"
        )
    save(established, json.dumps(identity))


def initialize() -> None:
    """Create fresh authorities only on this explicit, resumable setup path."""
    print("Preparing protected Atlas authority volumes", flush=True)
    checkpoint = STATE / "initializing"
    if (VAULT_SECRETS / "recovery.json").exists() and not checkpoint.exists():
        raise RuntimeError(
            "Established infrastructure requires Compose startup/recovery, not initialization"
        )
    names = ["openvox_" + name for name in OPENVOX_VOLUMES] + list(VAULT_VOLUMES)
    existing = set(run(DOCKER + ["volume", "ls", "--format", "{{.Name}}"]).splitlines())
    allocated = STATE / "volumes-created"
    if allocated.exists() and any("infra_" + name not in existing for name in names):
        raise RuntimeError(
            "Established volume missing; restore it instead of creating new state"
        )
    if not checkpoint.exists():
        if any("infra_" + n in existing for n in names):
            raise RuntimeError(
                "Existing infrastructure state requires Compose startup/recovery, not initialization"
            )
        save(checkpoint, "atlas.home.arpa\n")
    for name in names:
        volume = "infra_" + name
        if volume not in existing:
            run(DOCKER + ["volume", "create", volume])
        uid = "999:999" if name.startswith("openvox_") else "100:100"
        run(
            DOCKER
            + [
                "run",
                "--rm",
                "--network",
                "none",
                "--user",
                "0:0",
                "--mount",
                f"type=volume,src={volume},dst=/volume",
                "--entrypoint",
                "/bin/sh",
                "infra-openvox:24001",
                "-c",
                'chown "$1" /volume && chmod 0700 /volume',
                "own",
                uid,
            ]
        )
    save(allocated, "\n".join(names) + "\n")
    VAULT_SECRETS.mkdir(mode=0o750, parents=True, exist_ok=True)
    bootstrap = STATE / "transport"
    if not bootstrap.exists():
        with tempfile.TemporaryDirectory(dir=STATE) as generated:
            run(
                [
                    RUBY,
                    str(SOURCE / "docker/openvox/transport.rb"),
                    "vault.home.arpa",
                    generated,
                ]
            )
            Path(generated).rename(bootstrap)
    install_transport()
    compose("up", "-d", "--no-deps", "vault")
    print("Initializing/unsealing the declared Vault state", flush=True)
    status = health()
    if not status["initialized"]:
        if (VAULT_SECRETS / "recovery.json").exists():
            raise RuntimeError(
                "Vault storage missing despite recovery state; restore it"
            )
        recovery = api(
            "PUT", "sys/init", {"secret_shares": 1, "secret_threshold": 1}, auth=False
        )
        save(VAULT_SECRETS / "recovery.json", json.dumps(recovery))
    recovery = json.loads((VAULT_SECRETS / "recovery.json").read_text())
    save(VAULT_SECRETS / "root-token", recovery["root_token"])
    volume_file("vault_unseal", "key", recovery["keys_base64"][0])
    if status["sealed"]:
        api("PUT", "sys/unseal", {"key": recovery["keys_base64"][0]}, auth=False)
    mount("pki", "pki", {"config": {"max_lease_ttl": "87600h"}})
    issuers = api("LIST", "pki/issuers", accepted=(200, 404))
    if not issuers.get("data", {}).get("keys"):
        keys = api("LIST", "pki/keys", accepted=(200, 404))
        if CA.read_text() != (bootstrap / "trust.pem").read_text() or keys.get(
            "data", {}
        ).get("keys"):
            raise RuntimeError(
                "Established root issuer missing; restore existing state"
            )
        api(
            "POST",
            "pki/root/generate/internal",
            {
                "common_name": "Atlas infrastructure root",
                "key_type": "ml-dsa",
                "parameter_set": "ml-dsa-44",
                "ttl": "87600h",
                "max_path_length": 1,
            },
        )
    root = api("GET", "pki/cert/ca")["data"]["certificate"]
    fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(root)).hexdigest()
    saved_ca = CA.read_text().split("-----END CERTIFICATE-----", 1)[0].strip()
    bootstrap_ca = (
        (bootstrap / "trust.pem")
        .read_text()
        .split("-----END CERTIFICATE-----", 1)[0]
        .strip()
    )
    if (
        saved_ca != bootstrap_ca
        and saved_ca != root.split("-----END CERTIFICATE-----", 1)[0].strip()
    ):
        raise RuntimeError("Established root identity changed")
    print(f"Vault-held root certificate SHA-256: {fingerprint}", flush=True)
    install_transport(root)
    volume_file("openvox_trust", "root.pem", root)
    volume_file(
        "openvox_trust", "root.crl", api("GET", "pki/cert/crl")["data"]["certificate"]
    )
    imported = openvox(
        "/bin/sh",
        "-c",
        "test ! -s /etc/puppetlabs/puppetserver/ca/ca_crt.pem || printf imported",
    )
    if not imported:
        csr = openvox(RUBY, "/infra/infra-pki.rb", "prepare")
        chain = api(
            "POST",
            "pki/root/sign-intermediate",
            {"csr": csr, "format": "pem_bundle", "ttl": "43800h", "max_path_length": 0},
        )["data"]["certificate"]
        volume_file("openvox_trust", "openvox-chain.pem", chain)
        openvox(RUBY, "/infra/infra-pki.rb", "import", "--root-sha256", fingerprint)
    services_intermediate()
    configure_access()
    print("Installing the root-signed Vault TLS identity", flush=True)
    api(
        "POST",
        "pki_int/roles/vault-transport",
        {
            "key_type": "ml-dsa",
            "parameter_set": "ml-dsa-44",
            "allowed_domains": ["vault.home.arpa"],
            "allow_bare_domains": True,
            "allow_subdomains": False,
            "allow_localhost": False,
            "allow_wildcard_certificates": False,
            "max_ttl": "720h",
            "key_usage": ["DigitalSignature"],
        },
    )
    if (VAULT_SECRETS / "fullchain.pem").read_text() == (
        bootstrap / "tls.crt"
    ).read_text():
        transport = api(
            "POST",
            "pki_int/issue/vault-transport",
            {"common_name": "vault.home.arpa", "ttl": "720h"},
        )["data"]
        for name, value in (
            ("key.pem", transport["private_key"]),
            (
                "fullchain.pem",
                transport["certificate"]
                + "\n"
                + "\n".join(transport["ca_chain"])
                + "\n",
            ),
        ):
            save(VAULT_SECRETS / name, value)
            os.chown(VAULT_SECRETS / name, 100100, 100100)
    public = run(
        [
            RUBY,
            "-ropenssl",
            "-e",
            "puts OpenSSL::PKey.read(File.read(ARGV.fetch(0))).public_to_pem",
            str(VAULT_SECRETS / "key.pem"),
        ],
    )
    (VAULT_SECRETS / "public").mkdir(mode=0o750, exist_ok=True)
    os.chown(VAULT_SECRETS / "public", 100100, 100100)
    save(VAULT_SECRETS / "public/vault.pub.pem", public)
    os.chown(VAULT_SECRETS / "public/vault.pub.pem", 100100, 100100)
    compose("restart", "vault")
    status = health()
    if status["sealed"]:
        api("PUT", "sys/unseal", {"key": recovery["keys_base64"][0]}, auth=False)
    save(CA, root)
    os.chown(CA, 100100, 100100)
    deploy_initial()
    checkpoint.unlink()


def configure_access() -> None:
    """Configure scoped compiler and single-use enrollment authorization."""
    mount("kv", "kv", {"options": {"version": "2"}})
    existing = api("GET", "kv/data/puppet", accepted=(200, 404))
    if not existing.get("data"):
        api(
            "POST",
            "kv/data/puppet",
            {"data": {"atlas_infra::marker": "atlas-infra-v1"}},
        )
    if "cert/" not in api("GET", "sys/auth")["data"]:
        api("POST", "sys/auth/cert", {"type": "cert"})
    for name in ("puppet-server", "puppet-enrollment"):
        policy = (SOURCE / f"docker/vault/scripts/policies/{name}.hcl").read_text()
        api("PUT", "sys/policies/acl/" + name, {"policy": policy})
    chain = openvox("/bin/cat", "/trust/openvox-chain.pem")
    api(
        "POST",
        "auth/cert/certs/puppet-server",
        {
            "certificate": chain,
            "allowed_common_names": ["openvox.home.arpa"],
            "allowed_dns_sans": ["openvox.home.arpa"],
            "token_policies": ["puppet-server"],
            "token_no_default_policy": True,
            "token_ttl": "15m",
        },
    )
    configure_transport_access(chain)


def configure_transport_access(chain: str) -> None:
    """Let Atlas's Puppet agent issue only the two service TLS identities."""
    api(
        "PUT",
        "sys/policies/acl/atlas-transport",
        {
            "policy": "\n".join(
                f'path "{path}" {{ capabilities = ["update"] }}'
                for path in (
                    "pki_int/issue/vault-transport",
                    "pki_freeipa/issue/ipa-transport",
                )
            )
            + '\npath "auth/token/lookup-self" { capabilities = ["read"] }'
        },
    )
    api(
        "POST",
        "auth/cert/certs/atlas-transport",
        {
            "certificate": chain,
            "allowed_common_names": ["atlas.home.arpa"],
            "allowed_dns_sans": ["atlas.home.arpa"],
            "token_policies": ["atlas-transport"],
            "token_no_default_policy": True,
            "token_ttl": "5m",
        },
    )


def deploy_initial() -> None:
    """Populate both declared environments and install the publication hook."""
    git("fetch", "origin", "dev")
    run(
        [
            "runuser",
            "-u",
            "user_l",
            "--",
            "git",
            "--git-dir=/opt/git/openvox/control.git",
            "fetch",
            str(SOURCE),
            "refs/remotes/origin/dev:refs/heads/dev",
        ]
    )
    for environment in ("production", "dev"):
        print(f"Deploying {environment} code, modules and generated types", flush=True)
        openvox(
            "/opt/puppetlabs/puppet/bin/r10k",
            "deploy",
            "environment",
            environment,
            "--modules",
            "--config",
            "/infra/infra-r10k.yaml",
        )
        openvox(
            PUPPET,
            "generate",
            "types",
            "--environment",
            environment,
            "--config",
            "/etc/puppetlabs/puppet/puppet.conf",
            "--codedir",
            "/etc/puppetlabs/code",
            "--vardir",
            "/opt/puppetlabs/server/data/puppetserver/server_data",
        )
    run(
        [
            "install",
            "-d",
            "-o",
            "user_l",
            "-g",
            "docker",
            "-m",
            "2775",
            "/opt/git/openvox/control.git/hooks",
        ]
    )
    run(
        [
            "install",
            "-o",
            "user_l",
            "-g",
            "docker",
            "-m",
            "0755",
            str(SOURCE / "docker/openvox/infra-host-post-receive"),
            "/opt/git/openvox/control.git/hooks/post-receive",
        ]
    )
    run(["rm", "-f", "/opt/git/openvox/control.git/hooks/pre-receive"])


def main() -> None:
    """Initialize fresh authorities on Atlas through the explicit setup command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("initialize",))
    parser.parse_args()
    os.umask(0o077)
    if os.geteuid() != 0 or socket.getfqdn() != "atlas.home.arpa":
        raise RuntimeError("Run as root on atlas.home.arpa")
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    STATE.chmod(0o700)
    if run(DOCKER + ["info", "--format", "{{.Name}}"]).strip() != socket.gethostname():
        raise RuntimeError("Refusing nonlocal Docker daemon")
    os.environ["OPENSSL_CONF"] = str(SOURCE / "docker/openvox/openssl.cnf")
    os.environ["DOCKER_DOMAIN"] = "home.arpa"
    with maintenance():
        initialize()
    print(
        "Authorities initialized; start services with docker compose up -d --wait",
        flush=True,
    )


if __name__ == "__main__":
    main()
