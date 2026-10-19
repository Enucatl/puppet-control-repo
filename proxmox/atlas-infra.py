#!/usr/bin/env python3
"""Explicitly create Atlas authorities, or start their established state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import time

SOURCE = Path(__file__).resolve().parents[1]
STATE = Path("/root/openvox-infra")
RUBY = "/opt/puppetlabs/puppet/bin/ruby"
PUPPET = "/opt/puppetlabs/bin/puppet"
DOCKER = ["docker", "--host", "unix:///var/run/docker.sock"]
OPENVOX_VOLUMES = ("ssl", "ca", "code", "data", "gems", "git", "r10k_cache", "trust")
VAULT_VOLUMES = ("vault_data", "vault_logs", "certificates", "vault_unseal")
LABEL = "home.arpa.authority=atlas"


def run(arguments: list[str], data: str | None = None) -> str:
    """Keep command diagnostics private because API output can contain secrets."""
    result = subprocess.run(
        arguments, input=data, text=True, capture_output=True, timeout=1800
    )
    if result.returncode:
        log = STATE / f"failure-{time.time_ns()}.log"
        log.write_text(result.stdout + result.stderr)
        log.chmod(0o600)
        raise RuntimeError(f"Command failed ({result.returncode}); private log: {log}")
    return result.stdout


def compose(*arguments: str, data: str | None = None) -> str:
    """Select only declared new services; never load a legacy .env file."""
    return run(
        DOCKER
        + [
            "compose",
            "--env-file",
            "/dev/null",
            "-p",
            "infra",
            "-f",
            str(SOURCE / "docker/docker-compose.yml"),
            "-f",
            str(SOURCE / "docker/openvox/infra-vault.override.yaml"),
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
                    "trust": str(STATE / "trust.pem"),
                    "token_file": str(STATE / "root-token"),
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
    """Run explicit setup with existing mounts, bypassing normal startup guards."""
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
    """Repair a complete saved TLS identity before trying to start Vault."""
    bootstrap = STATE / "transport"
    saved = STATE / "vault-transport.json"
    if saved.exists():
        transport = json.loads(saved.read_text())
        certificate = transport["certificate"] + "\n" + "\n".join(transport["ca_chain"])
        key = transport["private_key"]
        root = root or (STATE / "root.pem").read_text()
    else:
        certificate = (bootstrap / "tls.crt").read_text()
        key = (bootstrap / "tls.key").read_text()
    trust = root or (bootstrap / "trust.pem").read_text()
    volume_file("certificates", "vault.key", key)
    volume_file("certificates", "vault_chain.crt", certificate)
    volume_file("certificates", "ca.crt", trust)
    # Both authenticated local pins permit an interrupted bootstrap TLS transition.
    save(STATE / "trust.pem", trust + "\n" + (bootstrap / "trust.pem").read_text())


def mount(name: str, kind: str, options: dict | None = None) -> None:
    """Create an absent mount without replacing existing issuer state."""
    if name + "/" not in api("GET", "sys/mounts")["data"]:
        api("POST", "sys/mounts/" + name, {"type": kind, **(options or {})})


def initialize() -> None:
    """Create fresh authorities only on this explicit, resumable setup path."""
    print("Preparing protected Atlas authority volumes", flush=True)
    if (STATE / "ready.json").exists():
        raise RuntimeError(
            "Established infrastructure requires start/recovery, not initialization"
        )
    checkpoint = STATE / "initializing"
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
                "Existing infrastructure state requires start/recovery, not initialization"
            )
        save(checkpoint, "atlas.home.arpa\n")
    for name in names:
        volume = "infra_" + name
        if volume not in existing:
            run(DOCKER + ["volume", "create", "--label", LABEL, volume])
        metadata = json.loads(run(DOCKER + ["volume", "inspect", volume]))[0]
        if metadata.get("Labels", {}).get("home.arpa.authority") != "atlas":
            raise RuntimeError(f"Refusing unmanaged volume: {volume}")
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
        if (STATE / "recovery.json").exists():
            raise RuntimeError(
                "Vault storage missing despite recovery state; restore it"
            )
        recovery = api(
            "PUT", "sys/init", {"secret_shares": 1, "secret_threshold": 1}, auth=False
        )
        save(STATE / "recovery.json", json.dumps(recovery))
    recovery = json.loads((STATE / "recovery.json").read_text())
    save(STATE / "root-token", recovery["root_token"])
    volume_file("vault_unseal", "key", recovery["keys_base64"][0])
    if status["sealed"]:
        api("PUT", "sys/unseal", {"key": recovery["keys_base64"][0]}, auth=False)
    mount("pki", "pki", {"config": {"max_lease_ttl": "87600h"}})
    issuers = api("LIST", "pki/issuers", accepted=(200, 404))
    if not issuers.get("data", {}).get("keys"):
        keys = api("LIST", "pki/keys", accepted=(200, 404))
        if (STATE / "root.sha256").exists() or keys.get("data", {}).get("keys"):
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
    if (STATE / "root.sha256").exists() and (
        STATE / "root.sha256"
    ).read_text() != fingerprint:
        raise RuntimeError("Established root identity changed")
    save(STATE / "root.sha256", fingerprint)
    print(f"Vault-held root certificate SHA-256: {fingerprint}", flush=True)
    save(STATE / "root.pem", root)
    save(STATE / "trust.pem", root + "\n" + (bootstrap / "trust.pem").read_text())
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
    mount("pki_int", "pki", {"config": {"max_lease_ttl": "43800h"}})
    issuers = api("LIST", "pki_int/issuers", accepted=(200, 404))
    if not issuers.get("data", {}).get("keys"):
        request = STATE / "vault-intermediate.json"
        if not request.exists():
            keys = api("LIST", "pki_int/keys", accepted=(200, 404))
            if keys.get("data", {}).get("keys"):
                raise RuntimeError(
                    "Missing saved intermediate CSR; recover request state"
                )
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
        csr = json.loads(request.read_text())["csr"]
        chain = api(
            "POST",
            "pki/root/sign-intermediate",
            {"csr": csr, "format": "pem_bundle", "ttl": "43800h", "max_path_length": 0},
        )["data"]["certificate"]
        api("POST", "pki_int/intermediate/set-signed", {"certificate": chain})
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
    if not (STATE / "vault-transport.json").exists():
        save(
            STATE / "vault-transport.json",
            json.dumps(
                api(
                    "POST",
                    "pki_int/issue/vault-transport",
                    {"common_name": "vault.home.arpa", "ttl": "720h"},
                )["data"]
            ),
        )
    transport = json.loads((STATE / "vault-transport.json").read_text())
    install_transport(root)
    public = run(
        [
            RUBY,
            "-ropenssl",
            "-e",
            'puts OpenSSL::PKey.read(JSON.parse(STDIN.read).fetch("private_key")).public_to_pem',
            "-rjson",
        ],
        json.dumps(transport),
    )
    volume_file("certificates", "public/vault.pub.pem", public)
    compose("restart", "vault")
    status = health()
    if status["sealed"]:
        api("PUT", "sys/unseal", {"key": recovery["keys_base64"][0]}, auth=False)
    save(STATE / "trust.pem", root)
    deploy_initial()
    save(
        STATE / "ready.json",
        json.dumps(
            {
                "root_sha256": fingerprint,
                "control_commit": git("rev-parse", "HEAD").strip(),
            }
        ),
    )
    start()
    print("Atlas Vault, unsealer and OpenVox are healthy", flush=True)


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
    source = (SOURCE / "docker/vault/scripts/08-vault-puppet-policy.sh").read_text()
    for name in ("puppet-server", "puppet-enrollment"):
        policy = source.split(f"vault policy write {name} - <<'EOF'\n", 1)[1].split(
            "\nEOF", 1
        )[0]
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


def deploy_initial() -> None:
    """Populate both declared environments before fail-on-missing-state startup."""
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
            "/bin/cp",
            "/infra/infra-hiera.yaml",
            f"/etc/puppetlabs/code/environments/{environment}/hiera.yaml",
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


def start() -> None:
    """Start established services without generating any authority material."""
    if not (STATE / "ready.json").is_file():
        raise RuntimeError(
            "Missing initialized state; explicitly initialize or restore it"
        )
    compose(
        "up",
        "-d",
        "--no-deps",
        "--wait",
        "--wait-timeout",
        "300",
        "vault",
        "vault-unsealer",
        "openvox",
    )


def main() -> None:
    """Require the protected Atlas target and explicit lifecycle operation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("initialize", "start"))
    action = parser.parse_args().action
    os.umask(0o077)
    if os.geteuid() != 0 or socket.getfqdn() != "atlas.home.arpa":
        raise RuntimeError("Run as root on atlas.home.arpa")
    if (
        Path("/etc/openvox-infra/promoted").read_text().strip()
        != "openvox-authority:atlas.home.arpa"
    ):
        raise RuntimeError("Promote VM 210 before creating authoritative state")
    STATE.mkdir(mode=0o700, exist_ok=True)
    STATE.chmod(0o700)
    if run(DOCKER + ["info", "--format", "{{.Name}}"]).strip() != socket.gethostname():
        raise RuntimeError("Refusing nonlocal Docker daemon")
    if git("status", "--porcelain").strip():
        raise RuntimeError("Activation requires a clean versioned checkout")
    os.environ["OPENSSL_CONF"] = str(SOURCE / "docker/openvox/openssl.cnf")
    os.environ["DOCKER_DOMAIN"] = "home.arpa"
    initialize() if action == "initialize" else start()


if __name__ == "__main__":
    main()
