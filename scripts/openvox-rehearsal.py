#!/usr/bin/env python3
"""Rehearse OpenVox with disposable local state and no production endpoints."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time

from openvox_rehearsal_recovery import recover

SOURCE = Path(__file__).resolve().parents[1]
WORK = Path(tempfile.gettempdir()) / f"openvox-rehearsal-{os.getuid()}"
STAGE = WORK / "repo"
PROJECT = "openvox-rehearsal"
DOCKER = ["docker", "--host", "unix:///var/run/docker.sock"]
RUBY = "/opt/puppetlabs/puppet/bin/ruby"
PYTHON = "/opt/puppetlabs/puppet/venv/bin/python"
PUPPET = "/opt/puppetlabs/bin/puppet"
BOOTSTRAP = "/opt/puppetlabs/server/data/puppetserver/server_data/bootstrap"
REPORT: dict = {"checks": [], "blockers": []}


def execute(
    arguments: list[str], data: str | bytes | None = None, timeout: int = 300
) -> str:
    """Capture command output privately, including failures that may hold secrets."""
    result = subprocess.run(
        arguments,
        input=data.encode() if isinstance(data, str) else data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={
            key: value
            for key, value in os.environ.items()
            if not key.startswith("COMPOSE_")
        },
        timeout=timeout,
    )
    if result.returncode:
        log = WORK / "private" / f"failure-{time.time_ns()}.log"
        log.parent.mkdir(mode=0o700, exist_ok=True)
        log.write_bytes(result.stdout + result.stderr)
        log.chmod(0o600)
        raise RuntimeError(
            f"Command failed (exit {result.returncode}); private log: {log}"
        )
    return result.stdout.decode()


def compose(*arguments: str, data: str | None = None, timeout: int = 300) -> str:
    """Use only the fixed local project and sanitized, explicit Compose inputs."""
    return execute(
        DOCKER
        + [
            "compose",
            "--env-file",
            "/dev/null",
            "--project-directory",
            str(STAGE / "docker/openvox"),
            "-f",
            str(STAGE / "docker/openvox/compose.yaml"),
            "--project-name",
            PROJECT,
            *arguments,
        ],
        data,
        timeout,
    )


def inside(
    service: str, arguments: list[str], data: str | None = None, *, once: bool = False
) -> str:
    """Run a command without passing tokens or private keys in its arguments."""
    if once:
        return compose(
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "--entrypoint",
            arguments[0],
            service,
            *arguments[1:],
            data=data,
        )
    return compose("exec", "-T", service, *arguments, data=data)


def write(service: str, path: str, contents: str, *, once: bool = False) -> None:
    """Write private material through stdin with restrictive file permissions."""
    inside(
        service,
        [
            PYTHON,
            "-c",
            "import os,pathlib,sys; os.umask(0o077); p=pathlib.Path(sys.argv[1]); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(sys.stdin.read())",
            path,
        ],
        contents,
        once=once,
    )


def api(
    service: str,
    method: str,
    path: str,
    body: dict | None = None,
    *,
    auth: bool = True,
    accepted: tuple[int, ...] = (200, 204),
) -> dict:
    """Send a verified TLS request through the service's loopback control path."""
    result = json.loads(
        inside(
            service,
            [PYTHON, "/rehearsal/vault-api.py"],
            json.dumps(
                {"method": method, "path": path, "body": body or {}, "auth": auth}
            ),
        )
    )
    if result["status"] not in accepted:
        raise RuntimeError(
            f"{service} API {method} {path} returned HTTP {result['status']}"
        )
    return result["body"]


def check(name: str, value: bool = True) -> None:
    """Record sanitized evidence and stop on a functional failure."""
    REPORT["checks"].append({"name": name, "passed": bool(value)})
    save_report()
    if not value:
        raise RuntimeError(f"Check failed: {name}")
    print(f"PASS {name}", flush=True)


def save_report() -> None:
    """Persist public results separately from private command diagnostics."""
    (WORK / "results.json").write_text(json.dumps(REPORT, indent=2) + "\n")


def stage() -> None:
    """Copy versioned inputs locally; NFS user namespaces cannot read the source."""
    daemon = execute(DOCKER + ["info", "--format", "{{.Name}}"]).strip()
    if daemon != socket.gethostname():
        raise RuntimeError("Rehearsal requires the local machine's Docker daemon")
    WORK.mkdir(mode=0o755, exist_ok=True)
    WORK.chmod(0o755)
    STAGE.mkdir(exist_ok=True)
    # Keep bind-mounted directory inodes stable while updating rehearsal inputs.
    if (STAGE / ".git").exists():
        shutil.rmtree(STAGE / ".git")
    archive = subprocess.check_output(
        ["git", "-C", str(SOURCE), "archive", "--format=tar", "HEAD"]
    )
    # Git archives contain no untracked .env files, runtime state, or credentials.
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as source:
        expected = {
            Path(member.name) for member in source.getmembers() if not member.isdir()
        }
        source.extractall(STAGE, filter="data")
    for path in (
        "Puppetfile",
        "scripts/autosign.py",
        "scripts/external_node_classifier.py",
        "scripts/openvox-rehearsal.py",
        "scripts/openvox_rehearsal_recovery.py",
        "scripts/openvox_offline_root.py",
    ):
        shutil.copy2(SOURCE / path, STAGE / path)
        expected.add(Path(path))
    runtime_files = {
        "Dockerfile",
        "compose.yaml",
        "root.hcl",
        "vault.hcl",
        "puppet.conf",
        "agent.conf",
        "webserver.conf",
        "ca.conf",
        "rehearsal.conf",
        "ready.rb",
        "transport.rb",
        "pki.rb",
        "check.rb",
        "vault-api.py",
        "deploy.sh",
        "openssl.cnf",
        "verify-crypto.rb",
        "acceptance.rb",
        "outbound-acceptance.rb",
        "tls-evidence.rb",
        "verify-state.rb",
    }
    for path in (SOURCE / "docker/openvox").rglob("*"):
        relative = path.relative_to(SOURCE / "docker/openvox")
        if (
            path.is_file()
            and not path.is_symlink()
            and (
                str(relative) in runtime_files
                or relative.parts[0] == "fixture"
                and path.suffix in (".pp", ".yaml")
            )
        ):
            target = STAGE / "docker/openvox" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            expected.add(Path("docker/openvox") / relative)
    for path in STAGE.rglob("*"):
        if (path.is_file() or path.is_symlink()) and path.relative_to(
            STAGE
        ) not in expected:
            path.unlink()
    for path in STAGE.rglob("*"):
        if path.is_dir():
            path.chmod(0o755)
        elif path.is_file():
            path.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)
    execute(["git", "-C", str(STAGE), "init", "--initial-branch=production"])
    execute(["git", "-C", str(STAGE), "add", "."])
    execute(
        [
            "git",
            "-C",
            str(STAGE),
            "-c",
            "user.name=OpenVox rehearsal",
            "-c",
            "user.email=rehearsal@example.invalid",
            "commit",
            "-m",
            "Sanitized rehearsal inputs",
        ]
    )
    REPORT["source_revision"] = execute(
        ["git", "-C", str(SOURCE), "rev-parse", "HEAD"]
    ).strip()
    REPORT["rehearsal_inputs_revision"] = execute(
        ["git", "-C", str(STAGE), "rev-parse", "HEAD"]
    ).strip()
    REPORT["image"] = (
        "ghcr.io/enucatl/openvox-server@sha256:b27583dfa1f3adc3f96bdcee192b783e0044cfb02837456d459b8ca6eca59b97"
    )
    REPORT["docker_host"] = daemon
    model = json.loads(compose("--profile", "*", "config", "--format", "json"))
    check(
        "no host ports or external networks",
        all(not service.get("ports") for service in model["services"].values())
        and all(not network.get("external") for network in model["networks"].values()),
    )


def start_vault(service: str, *, fresh: bool = False) -> None:
    """Explicitly initialize empty test Vault or unseal its existing identity."""
    exists = inside(
        service, ["sh", "-c", "test ! -e /state/tls.key || printf existing"], once=True
    )
    if not exists:
        if not fresh:
            raise RuntimeError(
                "Missing established Vault identity; use explicit rehearsal initialization"
            )
        inside(
            service,
            [RUBY, "/rehearsal/transport.rb", f"{service}.rehearsal.test"],
            once=True,
        )
    compose("up", "-d", "--no-deps", service)
    deadline = time.monotonic() + 40
    while True:
        try:
            health = api(
                service, "GET", "sys/health", auth=False, accepted=(200, 501, 503)
            )
            break
        except RuntimeError:
            if time.monotonic() > deadline:
                raise
            time.sleep(1)
    if not health["initialized"]:
        if not fresh:
            raise RuntimeError(
                "Missing established Vault storage; refusing to create a replacement authority"
            )
        recovery = api(
            service,
            "PUT",
            "sys/init",
            {"secret_shares": 1, "secret_threshold": 1},
            auth=False,
        )
        write(service, "/state/recovery.json", json.dumps(recovery))
    else:
        recovery = json.loads(inside(service, ["cat", "/state/recovery.json"]))
    if health["sealed"]:
        api(
            service,
            "PUT",
            "sys/unseal",
            {"key": recovery["keys_base64"][0]},
            auth=False,
        )


def mount(service: str, name: str, kind: str, options: dict | None = None) -> bool:
    """Create an absent mount, leaving restored authorities intact."""
    mounts = api(service, "GET", "sys/mounts")["data"]
    if name + "/" in mounts:
        return False
    api(service, "POST", "sys/mounts/" + name, {"type": kind, **(options or {})})
    return True


def initialize() -> None:
    """Create the fresh hierarchy only through the explicit rehearsal command."""
    print("Preparing offline root and isolated online Vault", flush=True)
    start_vault("root", fresh=True)
    if mount("root", "pki", "pki", {"config": {"max_lease_ttl": "8760h"}}):
        api(
            "root",
            "POST",
            "pki/root/generate/internal",
            {
                "common_name": "Offline OpenVox rehearsal root",
                "key_type": "ml-dsa",
                "parameter_set": "ml-dsa-44",
                "ttl": "8760h",
                "max_path_length": 1,
            },
        )
    root = api("root", "GET", "pki/cert/ca")["data"]["certificate"]
    crl = api("root", "GET", "pki/cert/crl")["data"]["certificate"]
    REPORT["root_sha256"] = hashlib.sha256(ssl.PEM_cert_to_DER_cert(root)).hexdigest()
    write("openvox", "/trust/root.pem", root, once=True)
    write("openvox", "/trust/root.crl", crl, once=True)
    csr = inside("openvox", [RUBY, "/rehearsal/pki.rb", "prepare"], once=True)
    existing = inside(
        "openvox",
        [
            "sh",
            "-c",
            "test ! -s /etc/puppetlabs/puppetserver/ca/ca_key.pem || printf existing",
        ],
        once=True,
    )
    if not existing:
        signed = api(
            "root",
            "POST",
            "pki/root/sign-intermediate",
            {"csr": csr, "format": "pem_bundle", "ttl": "720h", "max_path_length": 0},
        )["data"]["certificate"]
        write("openvox", BOOTSTRAP + "/import.pem", signed, once=True)
        write("openvox", "/trust/openvox-chain.pem", signed, once=True)
        inside("openvox", [RUBY, "/rehearsal/pki.rb", "check-issuer"], once=True)
        inside(
            "openvox",
            [
                RUBY,
                "-e",
                "require 'openssl'; c=OpenSSL::X509::Certificate.new(File.read(ARGV[0])); k=OpenSSL::PKey.read(File.read(ARGV[1])); r=OpenSSL::X509::CRL.new; r.version=1; r.issuer=c.subject; r.last_update=Time.now-60; r.next_update=Time.now+86400; r.add_extension(OpenSSL::X509::Extension.new('crlNumber',OpenSSL::ASN1::Integer(0).to_der)); r.sign(k,nil); File.write(ARGV[2],r.to_pem+File.read('/trust/root.crl'))",
                BOOTSTRAP + "/import.pem",
                BOOTSTRAP + "/intermediate.key",
                BOOTSTRAP + "/crls.pem",
            ],
            once=True,
        )
        inside(
            "openvox",
            [
                "/opt/puppetlabs/bin/puppetserver",
                "ca",
                "import",
                "--config",
                "/etc/puppetlabs/puppet/puppet.conf",
                "--private-key",
                BOOTSTRAP + "/intermediate.key",
                "--cert-bundle",
                BOOTSTRAP + "/import.pem",
                "--crl-chain",
                BOOTSTRAP + "/crls.pem",
                "--subject-alt-names",
                "openvox.rehearsal.test",
            ],
            once=True,
        )
        inside("openvox", [RUBY, "/rehearsal/pki.rb", "chain"], once=True)
    check("offline-root-signed OpenVox CA imported with fresh server keys")
    start_vault("vault", fresh=True)
    if mount("vault", "pki_int", "pki", {"config": {"max_lease_ttl": "720h"}}):
        csr = api(
            "vault",
            "POST",
            "pki_int/intermediate/generate/internal",
            {
                "common_name": "FreeIPA rehearsal intermediate",
                "key_type": "ml-dsa",
                "parameter_set": "ml-dsa-44",
            },
        )["data"]["csr"]
        signed = api(
            "root",
            "POST",
            "pki/root/sign-intermediate",
            {"csr": csr, "format": "pem_bundle", "ttl": "720h", "max_path_length": 0},
        )["data"]["certificate"]
        api("vault", "POST", "pki_int/intermediate/set-signed", {"certificate": signed})
        api(
            "vault",
            "POST",
            "pki_int/roles/vault-transport",
            {
                "key_type": "ml-dsa",
                "parameter_set": "ml-dsa-44",
                "allowed_domains": ["vault.rehearsal.test", "localhost"],
                "allow_bare_domains": True,
                "allow_subdomains": False,
                "allow_localhost": True,
                "allow_wildcard_certificates": False,
                "max_ttl": "24h",
                "key_usage": ["DigitalSignature"],
            },
        )
        transport = api(
            "vault",
            "POST",
            "pki_int/issue/vault-transport",
            {
                "common_name": "vault.rehearsal.test",
                "alt_names": "localhost",
                "ttl": "24h",
            },
        )["data"]
        write(
            "vault",
            "/state/tls.crt",
            transport["certificate"] + "\n" + "\n".join(transport["ca_chain"]),
        )
        write("vault", "/state/tls.key", transport["private_key"])
        write("vault", "/state/trust.pem", root)
        compose("restart", "vault")
        time.sleep(2)
        start_vault("vault")
    check("separate FreeIPA intermediate held by online Vault")

    compose("stop", "root")
    check("offline authority stopped before enrollment and catalog workflows")
    configure_access()


def configure_access() -> None:
    """Mirror the existing least-privilege roles using isolated identities."""
    mount("vault", "kv", "kv", {"options": {"version": "2"}})
    api(
        "vault",
        "POST",
        "kv/data/puppet",
        {"data": {"rehearsal::marker": "openvox-rehearsal-v1"}},
    )
    auth = api("vault", "GET", "sys/auth")["data"]
    if "cert/" not in auth:
        api("vault", "POST", "sys/auth/cert", {"type": "cert"})
        auth = api("vault", "GET", "sys/auth")["data"]
    identity = "{{identity.entity.aliases." + auth["cert/"]["accessor"] + ".name}}"
    policy_source = (
        SOURCE / "docker/vault/scripts/08-vault-puppet-policy.sh"
    ).read_text()
    for policy in ("puppet-server", "puppet-enrollment"):
        policy_text = policy_source.split(
            f"vault policy write {policy} - <<'EOF'\n", 1
        )[1].split("\nEOF", 1)[0]
        api("vault", "PUT", "sys/policies/acl/" + policy, {"policy": policy_text})
    api(
        "vault",
        "PUT",
        "sys/policies/acl/puppet",
        {
            "policy": 'path "pki_int/issue/puppet" { capabilities = ["update"] }\n'
            'path "auth/token/lookup-self" { capabilities = ["read"] }'
        },
    )
    chain = inside("openvox", ["cat", "/trust/openvox-chain.pem"], once=True)
    for role, certname, policies in (
        ("puppet-server", "openvox.rehearsal.test", ["puppet-server"]),
        ("puppet", "rehearsal.home.arpa", ["puppet"]),
    ):
        api(
            "vault",
            "POST",
            "auth/cert/certs/" + role,
            {
                "certificate": chain,
                "allowed_common_names": [certname],
                "allowed_dns_sans": [certname],
                "token_policies": policies,
                "token_no_default_policy": True,
                "token_ttl": "15m",
            },
        )
    api(
        "vault",
        "POST",
        "pki_int/roles/puppet",
        {
            "key_type": "ml-dsa",
            "parameter_set": "ml-dsa-44",
            "allowed_domains": [identity],
            "allowed_domains_template": True,
            "allow_bare_domains": True,
            "allow_subdomains": True,
            "allow_wildcard_certificates": False,
            "allow_glob_domains": False,
            "allow_localhost": False,
            "allow_ip_sans": False,
            "max_ttl": "1h",
            "key_usage": ["DigitalSignature"],
        },
    )


def agent_run() -> int:
    """Run the actual native agent, retaining potentially sensitive logs privately."""
    output = inside(
        "agent",
        [
            "sh",
            "-c",
            'install -D -m 0644 /trust/openvox-chain.pem /agent/ssl/certs/ca.pem || exit; set +e; /opt/puppetlabs/puppet/bin/ruby -r/rehearsal/tls-evidence.rb /opt/puppetlabs/puppet/bin/puppet agent --confdir /etc/puppetlabs/puppet --localcacert /agent/ssl/certs/ca.pem --test --detailed-exitcodes --color false > /agent/last-run.log 2>&1; code=$?; printf "%s" "$code"',
        ],
    )
    return int(output)


def enroll() -> None:
    """Enroll with an orphaned single-use token bound to the exact certname."""
    existing = inside(
        "agent",
        [
            "sh",
            "-c",
            "test ! -s /agent/ssl/certs/rehearsal.home.arpa.pem || printf existing",
        ],
    )
    if existing:
        check(
            "established agent identity resumes catalog application",
            agent_run() in (0, 2),
        )
        return
    token = api(
        "vault",
        "POST",
        "auth/token/create-orphan",
        {
            "policies": ["puppet-enrollment"],
            "ttl": "2h",
            "renewable": False,
            "no_default_policy": True,
            "num_uses": 1,
            "type": "service",
            "meta": {"certname": "rehearsal.home.arpa"},
        },
    )["auth"]["client_token"]
    write(
        "agent",
        "/etc/puppetlabs/puppet/csr_attributes.yaml",
        "custom_attributes:\n  1.2.840.113549.1.9.7: " + json.dumps(token) + "\n",
    )
    try:
        result = agent_run()
        check("real autosigner enrollment and first catalog apply", result in (0, 2))
        # Successful enrollment can remove the saved CSR; keep testing the consumed token.
        csr = inside(
            "agent",
            [
                RUBY,
                "-ropenssl",
                "-rjson",
                "-e",
                "path='/agent/ssl/certificate_requests/rehearsal.home.arpa.pem'; "
                "if File.file?(path); puts File.read(path); else; "
                "key=OpenSSL::PKey.read(File.read('/agent/ssl/private_keys/rehearsal.home.arpa.pem')); "
                "csr=OpenSSL::X509::Request.new; csr.version=0; "
                "csr.subject=OpenSSL::X509::Name.parse('/CN=rehearsal.home.arpa'); csr.public_key=key; "
                "csr.add_attribute(OpenSSL::X509::Attribute.new('challengePassword',OpenSSL::ASN1::Set.new([OpenSSL::ASN1::UTF8String.new(JSON.parse(STDIN.read))]))); "
                "csr.sign(key,nil); raise unless csr.verify(key); puts csr.to_pem; end",
            ],
            json.dumps(token),
        )
        replay = inside(
            "openvox",
            [
                PYTHON,
                "-c",
                "import sys;sys.path.insert(0,'/helpers');from autosign import validate_csr;from hvac.exceptions import Forbidden;\ntry: validate_csr('rehearsal.home.arpa',sys.stdin.buffer.read(),'https://vault.rehearsal.test:8200','/trust/root.pem','home.arpa')\nexcept Forbidden: print('denied')\nexcept Exception: print('replay-check-failed')\nelse: print('approved')",
            ],
            csr,
        )
        check("single-use enrollment token replay denied", replay.strip() == "denied")
    finally:
        inside("agent", ["rm", "-f", "/etc/puppetlabs/puppet/csr_attributes.yaml"])
    check("subsequent catalog converges", agent_run() == 0)
    check(
        "catalog obtained real Vault Hiera value",
        inside("agent", ["cat", "/tmp/openvox-rehearsal-marker"]).strip()
        == "openvox-rehearsal-v1",
    )


def identity() -> str:
    """Fingerprint public identities for recreation and restore comparisons."""
    output = inside(
        "openvox",
        [
            RUBY,
            "-ropenssl",
            "-rjson",
            "-e",
            "puts JSON.generate(ARGV.map{|p| OpenSSL::Digest::SHA256.hexdigest(OpenSSL::X509::Certificate.new(File.read(p)).to_der)})",
            "/etc/puppetlabs/puppetserver/ca/ca_crt.pem",
            "/etc/puppetlabs/puppet/ssl/certs/openvox.rehearsal.test.pem",
        ],
    )
    return output.strip()


def request(
    service: str,
    host: str,
    port: int,
    path: str,
    identity: str,
    *,
    method: str = "GET",
    body: str = "",
) -> dict:
    """Exercise a real verified TLS endpoint with the selected existing identity."""
    return json.loads(
        inside(
            service,
            [
                "env",
                "REQUEST_METHOD=" + method,
                RUBY,
                "/rehearsal/check.rb",
                host,
                str(port),
                path,
                identity,
            ],
            body,
        )
    )


def lifecycle() -> None:
    """Exercise renewal, signed CRL reload, revocation and fresh replacement."""
    print("Testing renewal, revocation and replacement", flush=True)
    cert = "/agent/ssl/certs/rehearsal.home.arpa.pem"
    old = json.loads(
        inside(
            "agent",
            [
                RUBY,
                "-ropenssl",
                "-rjson",
                "-e",
                "c=OpenSSL::X509::Certificate.new(File.read(ARGV[0])); "
                "puts JSON.generate(serial:c.serial.to_s,public_key:OpenSSL::Digest::SHA256.hexdigest(c.public_key.public_to_der))",
                cert,
            ],
        )
    )
    renewal = request(
        "agent",
        "openvox.rehearsal.test",
        8140,
        "/puppet-ca/v1/certificate_renewal",
        "agent",
        method="POST",
        body="{}",
    )
    check("enrolled identity renews through the CA API", renewal["status"] == 200)
    write("agent", cert, renewal["body"])
    current = json.loads(
        inside(
            "agent",
            [
                RUBY,
                "-ropenssl",
                "-rjson",
                "-e",
                "c=OpenSSL::X509::Certificate.new(File.read(ARGV[0])); "
                "puts JSON.generate(serial:c.serial.to_s,public_key:OpenSSL::Digest::SHA256.hexdigest(c.public_key.public_to_der))",
                cert,
            ],
        )
    )
    check(
        "renewal changes serial, preserves key and remains functional",
        current["serial"] != old["serial"]
        and current["public_key"] == old["public_key"]
        and agent_run() == 0,
    )
    revoked = request(
        "openvox",
        "openvox.rehearsal.test",
        8140,
        "/puppet-ca/v1/certificate_status/rehearsal.home.arpa",
        "server",
        method="PUT",
        body=json.dumps({"desired_state": "revoked"}),
    )
    check("authorized CA revocation succeeds", revoked["status"] == 204)
    crl = inside(
        "openvox",
        [
            RUBY,
            "-ropenssl",
            "-e",
            "puts OpenSSL::X509::CRL.new(File.read('/etc/puppetlabs/puppetserver/ca/ca_crl.pem')).to_pem",
        ],
    )
    inside(
        "openvox",
        [
            RUBY,
            "/rehearsal/verify-crypto.rb",
            "--crl",
            "/dev/stdin",
        ],
        crl,
    )
    api("vault", "POST", "auth/cert/crls/openvox", {"crl": crl})
    check(
        "ML-DSA CRL published to Vault",
        request(
            "agent",
            "vault.rehearsal.test",
            8200,
            "/v1/auth/cert/login",
            "agent",
            method="POST",
            body='{"name":"puppet"}',
        )["status"]
        == 400,
    )
    deadline = time.monotonic() + 30
    while agent_run() != 1:
        if time.monotonic() > deadline:
            raise RuntimeError("Revoked agent remained accepted after CRL reload")
        time.sleep(1)
    check("OpenVox reloads CRL and denies revoked identity")
    cleaned = request(
        "openvox",
        "openvox.rehearsal.test",
        8140,
        "/puppet-ca/v1/certificate_status/rehearsal.home.arpa",
        "server",
        method="DELETE",
    )
    check("authorized cleanup permits replacement enrollment", cleaned["status"] == 204)
    inside(
        "agent",
        [
            "sh",
            "-c",
            "umask 077; mkdir -p /agent/revoked; mv /agent/ssl /agent/revoked/ssl-$(date +%s)",
        ],
    )
    enroll()
    new = json.loads(
        inside(
            "agent",
            [
                RUBY,
                "-ropenssl",
                "-rjson",
                "-e",
                "c=OpenSSL::X509::Certificate.new(File.read(ARGV[0])); "
                "puts JSON.generate(serial:c.serial.to_s,public_key:OpenSSL::Digest::SHA256.hexdigest(c.public_key.public_to_der))",
                cert,
            ],
        )
    )
    check(
        "replacement identity has a fresh certificate and key",
        new["serial"] not in (old["serial"], current["serial"])
        and new["public_key"] != old["public_key"],
    )


def scheduled_run() -> None:
    """Wait for two actual agent timer runs and retain their logs privately."""
    count = inside(
        "agent",
        [
            "sh",
            "-c",
            'set +e; timeout --signal=TERM 16 /opt/puppetlabs/puppet/bin/ruby -r/rehearsal/tls-evidence.rb /opt/puppetlabs/puppet/bin/puppet agent --confdir /etc/puppetlabs/puppet --no-daemonize --runinterval 5 --no-splay --verbose --color false > /agent/scheduled.log 2>&1; grep -c "Applied catalog in" /agent/scheduled.log || true',
        ],
    )
    check(
        "scheduled agent completes at least two convergent runs",
        int(count.strip()) >= 2,
    )


def run() -> None:
    """Exercise disposable initialization, real workflows and lifecycle persistence."""
    stage()
    absent = subprocess.run(
        DOCKER + ["run", "--rm", "--network", "none", "openvox-rehearsal-tools:24001"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=20,
    )
    check(
        "normal startup refuses missing CA and server identity",
        absent.returncode != 0
        and b"Restore or explicitly initialize server identity before startup"
        in absent.stdout,
    )
    initialize()
    print("Deploying sanitized real control repository with r10k", flush=True)
    compose("run", "--rm", "--no-deps", "-T", "deploy", timeout=900)
    check("r10k deploys real modules and safe production/dev catalogs")
    compose(
        "up",
        "-d",
        "--no-deps",
        "--wait",
        "--wait-timeout",
        "240",
        "openvox",
        timeout=300,
    )
    compose("up", "-d", "--no-deps", "agent")
    REPORT["tools_image_id"] = execute(
        DOCKER
        + ["image", "inspect", "openvox-rehearsal-tools:24001", "--format", "{{.Id}}"]
    ).strip()
    REPORT["build_manifest"] = json.loads(
        inside("openvox", ["cat", "/usr/share/openvox/build-manifest.json"])
    )
    REPORT["runtimes"] = {
        "native": inside(
            "openvox",
            [
                RUBY,
                "-ropenssl",
                "-e",
                "puts RUBY_DESCRIPTION, OpenSSL::VERSION, OpenSSL::OPENSSL_LIBRARY_VERSION",
            ],
        ).strip(),
        "python": json.loads(
            inside(
                "openvox",
                [
                    PYTHON,
                    "-c",
                    "import json,sys,ssl,cryptography;print(json.dumps({'python':sys.version,'ssl':ssl.OPENSSL_VERSION,'cryptography':cryptography.__version__}))",
                ],
            )
        ),
        "java": inside("openvox", ["sh", "-c", '"$JAVA_BIN" -version 2>&1']).strip(),
        "vault": inside("vault", ["vault", "version"]).strip(),
        "vault_module_revision": inside(
            "openvox",
            [
                "git",
                "-C",
                "/etc/puppetlabs/code/rehearsal-environments/production/modules/vault_secrets",
                "rev-parse",
                "HEAD",
            ],
        ).strip(),
    }
    REPORT["resources"] = [
        json.loads(line)
        for line in execute(
            DOCKER
            + [
                "stats",
                "--no-stream",
                "--format",
                "json",
                PROJECT + "-openvox-1",
                PROJECT + "-vault-1",
                PROJECT + "-agent-1",
            ]
        ).splitlines()
    ]
    for service, sections in (("openvox", ("server", "agent")), ("agent", ("agent",))):
        for section in sections:
            effective = inside(
                service,
                [
                    PUPPET,
                    "config",
                    "print",
                    "key_type",
                    "--section",
                    section,
                    "--config",
                    "/etc/puppetlabs/puppet/puppet.conf",
                ],
            )
            check(
                f"{service} effective {section} key_type=mldsa44",
                effective.strip() == "mldsa44",
            )
    enroll()
    acceptance = json.loads(inside("agent", [RUBY, "/rehearsal/acceptance.rb"]))
    for name, passed in acceptance["checks"].items():
        check(name, passed)
    REPORT["transports"] = acceptance.get("handshakes", [])
    REPORT["observations"] = acceptance.get("observations", {})
    original = identity()
    compose(
        "up",
        "-d",
        "--no-deps",
        "--force-recreate",
        "--wait",
        "--wait-timeout",
        "240",
        "openvox",
        timeout=300,
    )
    check("recreation preserves CA and server identities", identity() == original)
    check("agent converges after server recreation", agent_run() == 0)
    api(
        "vault",
        "POST",
        "kv/data/puppet",
        {"data": {"rehearsal::marker": "openvox-rehearsal-v2"}},
    )
    compose("run", "--rm", "--no-deps", "-T", "deploy", timeout=900)
    check(
        "Git push and environment-cache invalidation expose updated Hiera data",
        agent_run() == 2,
    )
    check("updated catalog converges", agent_run() == 0)
    # Keep the fixture's initial value for replacement and independent restore.
    api(
        "vault",
        "POST",
        "kv/data/puppet",
        {"data": {"rehearsal::marker": "openvox-rehearsal-v1"}},
    )
    check("catalog returns to the initial rehearsal value", agent_run() in (0, 2))
    scheduled_run()
    lifecycle()
    # Existing volumes are the only authority source on normal startup.
    compose("stop", "vault")
    start_vault("vault")
    check("online Vault restart preserves hierarchy and Hiera", agent_run() == 0)
    state = json.loads(inside("openvox", [RUBY, "/rehearsal/verify-state.rb"]))
    check(
        "runtime keys, certificate chain, issuer constraints and CRLs verified",
        state["ok"],
    )
    REPORT["runtime_state"] = state
    outbound_acceptance()
    REPORT["outbound"] = json.loads((WORK / "outbound-transports.json").read_text())
    check("all 15 actual Python and JRuby outbound TLS checks pass")
    REPORT["recovery"] = recover(sys.modules[__name__])
    REPORT["blockers"].extend(
        [
            "Host reboot and independent Atlas restore require their dedicated execution environments.",
            "BCJSSE conditional disabledAlgorithms restrictions need separate negative-policy evidence.",
            "Native fleet OS/architecture compatibility is not established by this disposable agent.",
            "Fresh remote deployment requires the published ML-DSA Vault Hiera transport and its resolved revision.",
        ]
    )
    REPORT["ready_for_production"] = False
    REPORT["limitations"] = [
        "Per-consumer BCJSSE conditional-policy semantics remain broader than the controlled transport tests.",
        "Offline-root lifecycle and static publication passed; real issuer URLs, freshness monitoring and independently protected recovery media remain operational work.",
        "Actual FreeIPA consumers, native guest/full-role catalogs, cold bootstrap and host reboot remain unverified.",
        "Infra configuration and native callers are prepared; production activation is separate from the disposable rehearsal.",
    ]
    save_report()
    print(
        f"Functional rehearsal passed; production readiness remains blocked. Report: {WORK / 'results.json'}",
        flush=True,
    )


def outbound_acceptance() -> None:
    """Test real client transports against disposable peers, preserving live config."""
    relative = Path("docker/openvox/outbound-acceptance.rb")
    shutil.copyfile(SOURCE / relative, STAGE / relative)
    result = json.loads(
        inside(
            "openvox",
            [
                "env",
                "OPENSSL_CONF=/dev/null",
                RUBY,
                "/rehearsal/outbound-acceptance.rb",
            ],
        )
    )
    path = WORK / "outbound-transports.json"
    path.write_text(json.dumps(result, indent=2) + "\n")
    results = result["results"]
    checks = [
        passed
        for scenario, values in results.items()
        for name, passed in values.items()
        if name.endswith("_policy_passed") or scenario == "positive_handshakes"
    ]
    if len(checks) != 15 or not all(checks):
        raise RuntimeError(
            f"Outbound TLS policy has unresolved checks; evidence: {path}"
        )
    print(f"PASS all 15 outbound transport checks; evidence: {path}", flush=True)


def main() -> None:
    """Select explicit initialization versus identity-preserving normal startup."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("run", "up", "stop", "status", "destroy", "transports")
    )
    parser.add_argument(
        "--restored", action="store_true", help="select the separate restored rehearsal"
    )
    arguments = parser.parse_args()
    global PROJECT, STAGE
    if arguments.restored:
        if arguments.command == "run":
            parser.error("run initializes only the original rehearsal project")
        PROJECT = "openvox-rehearsal-restore"
        STAGE = WORK / "restored-repo"
    WORK.mkdir(mode=0o755, exist_ok=True)
    try:
        if arguments.command == "run":
            run()
        elif arguments.command == "transports":
            outbound_acceptance()
        elif arguments.command == "up":
            start_vault("vault")
            compose(
                "up", "-d", "--no-deps", "--wait", "--wait-timeout", "240", "openvox"
            )
            compose("up", "-d", "--no-deps", "agent")
        elif arguments.command == "stop":
            compose("--profile", "ceremony", "--profile", "tools", "stop")
        elif arguments.command == "destroy":
            compose(
                "--profile",
                "ceremony",
                "--profile",
                "tools",
                "down",
                "--volumes",
                "--remove-orphans",
            )
            if arguments.restored and STAGE.exists():
                shutil.rmtree(STAGE)
        else:
            print(compose("--profile", "ceremony", "--profile", "tools", "ps", "--all"))
    except (RuntimeError, subprocess.TimeoutExpired) as error:
        if arguments.command == "run":
            REPORT["error"] = str(error)
            REPORT["ready_for_production"] = False
            save_report()
        else:
            (WORK / "management-error.json").write_text(
                json.dumps(
                    {
                        "command": arguments.command,
                        "project": PROJECT,
                        "error": str(error),
                    }
                )
                + "\n"
            )
        raise SystemExit(str(error)) from None


if __name__ == "__main__":
    main()
