"""Verify certificate namespaces against disposable Vault with real TLS login."""

from __future__ import annotations

import os
import shutil
import socket
import ssl
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import hvac
import pytest

ROOT = Path(__file__).resolve().parents[1]
NODES = (
    "complex.home.arpa",
    "docker.home.arpa",
    "forbearance.home.arpa",
    "proxmox.home.arpa",
    "proxmox-cortex.home.arpa",
)
DOCKER_ROLE = "puppet-docker.home.arpa"


@pytest.fixture(scope="module")
def vault(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[hvac.Client, dict[str, hvac.Client]]]:
    """Install the actual policy in isolated Vault and authenticate Puppet nodes."""
    binary = shutil.which("vault")
    if binary is None:
        pytest.skip("Vault executable is required for disposable integration tests")
    directory = tmp_path_factory.mktemp("puppet-pki-vault")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    address = f"https://127.0.0.1:{port}"
    ca = directory / "vault-ca.pem"
    environment = {
        "PATH": os.environ["PATH"],
        "HOME": str(directory),
        "VAULT_ADDR": address,
        "VAULT_TOKEN": "disposable-pki-root",
        "VAULT_CACERT": str(ca),
    }
    with (directory / "server.log").open("w") as log:
        process = subprocess.Popen(
            [
                binary,
                "server",
                "-dev",
                "-dev-tls",
                f"-dev-tls-cert-dir={directory}",
                "-dev-root-token-id=disposable-pki-root",
                f"-dev-listen-address=127.0.0.1:{port}",
            ],
            env=environment,
            cwd=directory,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 15
            while True:
                try:
                    context = ssl.create_default_context(cafile=str(ca))
                    with urlopen(
                        f"{address}/v1/sys/health", context=context, timeout=1
                    ) as response:
                        assert response.status == 200
                    break
                except URLError, FileNotFoundError:
                    if process.poll() is not None or time.monotonic() >= deadline:
                        pytest.fail("Disposable TLS Vault failed to start")
                    time.sleep(0.05)
            root = hvac.Client(
                url=address, token=environment["VAULT_TOKEN"], verify=str(ca)
            )
            root.sys.enable_secrets_engine("kv", path="kv", options={"version": "2"})
            for path in ("puppet", "wolf"):
                root.secrets.kv.v2.create_or_update_secret(
                    path=path, secret={"value": "fixture-secret"}, mount_point="kv"
                )
            for mount in ("pki_int", "puppet_ca"):
                root.sys.enable_secrets_engine(
                    "pki",
                    path=mount,
                    config={"default_lease_ttl": "1h", "max_lease_ttl": "87600h"},
                )
                root.secrets.pki.generate_root(
                    "internal",
                    common_name=mount,
                    extra_params={"ttl": "87600h"},
                    mount_point=mount,
                )
            broad_role = {
                "allowed_domains": ["home.arpa"],
                "allow_subdomains": True,
                "allow_wildcard_certificates": True,
                "ttl": "1h",
            }
            root.secrets.pki.create_or_update_role(
                "general", extra_params=broad_role, mount_point="pki_int"
            )
            root.secrets.pki.create_or_update_role(
                "nodes", extra_params=broad_role, mount_point="puppet_ca"
            )
            root.sys.enable_auth_method("cert")
            root.write(
                "auth/cert/certs/puppet",
                certificate=root.read("puppet_ca/cert/ca")["data"]["certificate"],
                allowed_common_names=list(NODES),
                allowed_dns_sans=list(NODES),
                token_policies=["puppet"],
            )
            root.sys.create_or_update_policy(
                "puppet",
                'path "pki_int/issue/general" { capabilities = ["update"] }\n'
                'path "kv/data/puppet" { capabilities = ["read"] }\n'
                'path "kv/data/wolf" { capabilities = ["read"] }',
            )
            clients = {}
            for node in NODES:
                certificate = root.secrets.pki.generate_certificate(
                    "nodes", common_name=node, mount_point="puppet_ca"
                )["data"]
                cert_file = directory / f"{node}.pem"
                key_file = directory / f"{node}.key"
                cert_file.write_text(certificate["certificate"])
                key_file.write_text(certificate["private_key"])
                client = hvac.Client(url=address, verify=str(ca))
                client.auth.cert.login(
                    name="puppet", cert_pem=str(cert_file), key_pem=str(key_file)
                )
                clients[node] = client
                for path in ("puppet", "wolf"):
                    assert client.read(f"kv/data/{path}")["data"]["data"] == {
                        "value": "fixture-secret"
                    }
            # Keep these pre-existing tokens through policy replacement.
            assert clients[NODES[0]].write(
                "pki_int/issue/general", common_name="*.home.arpa"
            )["data"]["certificate"]
            for _ in range(2):
                result = subprocess.run(
                    [
                        "bash",
                        str(ROOT / "docker/vault/scripts/08-vault-puppet-policy.sh"),
                    ],
                    env=environment,
                    cwd=directory,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                assert result.returncode == 0, result.stderr
            yield root, clients
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.mark.parametrize("node", NODES)
@pytest.mark.parametrize("prefix", ("", "service.", "nested.service."))
def test_node_can_issue_own_names(
    vault: tuple[hvac.Client, dict[str, hvac.Client]], node: str, prefix: str
) -> None:
    """Certificate identity authorizes its hostname and named descendants."""
    _, clients = vault
    assert clients[node].write("pki_int/issue/puppet", common_name=f"{prefix}{node}")[
        "data"
    ]["certificate"]


@pytest.mark.parametrize(
    "payload",
    [
        {"common_name": "*.complex.home.arpa"},
        {"common_name": "wild*.complex.home.arpa"},
        {"common_name": "user@complex.home.arpa"},
        {"common_name": "*.service.complex.home.arpa"},
        {"common_name": "docker.home.arpa"},
        {"common_name": "complex.home.arpa.evil.example"},
        {"common_name": "home.arpa"},
        {"common_name": "localhost"},
        {"common_name": "127.0.0.1"},
        {"common_name": "complex.home.arpa", "alt_names": "docker.home.arpa"},
        {
            "common_name": "docker.home.arpa",
            "alt_names": "complex.home.arpa",
            "exclude_cn_from_sans": True,
        },
        {
            "common_name": "complex.home.arpa",
            "alt_names": "docker.home.arpa",
            "exclude_cn_from_sans": True,
        },
        {"common_name": "complex.home.arpa", "alt_names": "*.complex.home.arpa"},
        {"common_name": "complex.home.arpa", "ip_sans": "127.0.0.1"},
        {"common_name": "complex.home.arpa", "uri_sans": "spiffe://other/node"},
    ],
)
def test_node_cannot_escape_namespace(
    vault: tuple[hvac.Client, dict[str, hvac.Client]], payload: dict[str, str | bool]
) -> None:
    """Role validation covers subject names and every requested SAN type."""
    _, clients = vault
    with pytest.raises(hvac.exceptions.InvalidRequest):
        clients["complex.home.arpa"].write("pki_int/issue/puppet", **payload)


@pytest.mark.parametrize("node", NODES)
@pytest.mark.parametrize(
    "path",
    ("issue/general", "sign/general", "sign/puppet", "roles/puppet", "roles/general"),
)
def test_existing_tokens_lose_broad_issuance_and_admin_access(
    vault: tuple[hvac.Client, dict[str, hvac.Client]], node: str, path: str
) -> None:
    """Policy replacement restricts tokens issued before the migration too."""
    _, clients = vault
    with pytest.raises(hvac.exceptions.Forbidden):
        clients[node].write(f"pki_int/{path}", common_name=node)


@pytest.mark.parametrize("node", ("complex.home.arpa", "forbearance.home.arpa"))
def test_only_docker_identity_can_use_wildcard_role(
    vault: tuple[hvac.Client, dict[str, hvac.Client]], node: str
) -> None:
    """Knowing the Docker role name does not authorize another node to use it."""
    _, clients = vault
    with pytest.raises(hvac.exceptions.Forbidden):
        clients[node].write(
            f"pki_int/issue/{DOCKER_ROLE}", common_name="*.docker.home.arpa"
        )


@pytest.mark.parametrize(
    "name",
    (
        "docker.home.arpa",
        "service.docker.home.arpa",
        "*.docker.home.arpa",
        "*.service.docker.home.arpa",
    ),
)
def test_docker_can_issue_wildcards_within_own_namespace(
    vault: tuple[hvac.Client, dict[str, hvac.Client]], name: str
) -> None:
    """Docker retains wildcard issuance under its authenticated hostname."""
    _, clients = vault
    assert clients["docker.home.arpa"].write(
        f"pki_int/issue/{DOCKER_ROLE}", common_name=name
    )["data"]["certificate"]


@pytest.mark.parametrize(
    "name", ("complex.home.arpa", "*.complex.home.arpa", "*.home.arpa", "localhost")
)
def test_docker_cannot_issue_other_namespaces(
    vault: tuple[hvac.Client, dict[str, hvac.Client]], name: str
) -> None:
    """Docker's wildcard exception does not grant another node's namespace."""
    _, clients = vault
    with pytest.raises(hvac.exceptions.InvalidRequest):
        clients["docker.home.arpa"].write(
            f"pki_int/issue/{DOCKER_ROLE}", common_name=name
        )


@pytest.mark.parametrize("role", ("puppet", DOCKER_ROLE))
def test_policy_without_certificate_identity_cannot_issue(
    vault: tuple[hvac.Client, dict[str, hvac.Client]], role: str
) -> None:
    """Attaching the shared policy alone cannot establish a node namespace."""
    root, _ = vault
    token = root.auth.token.create(policies=["puppet"])["auth"]["client_token"]
    client = hvac.Client(
        url=root.url, token=token, verify=root.adapter._kwargs["verify"]
    )
    with pytest.raises((hvac.exceptions.Forbidden, hvac.exceptions.InvalidRequest)):
        client.write(f"pki_int/issue/{role}", common_name="docker.home.arpa")


@pytest.mark.parametrize("node", NODES)
@pytest.mark.parametrize("path", ("puppet", "wolf"))
def test_existing_agent_tokens_lose_kv_access(
    vault: tuple[hvac.Client, dict[str, hvac.Client]], node: str, path: str
) -> None:
    """Replacing the shared policy removes KV access from existing tokens."""
    _, clients = vault
    with pytest.raises(hvac.exceptions.Forbidden):
        clients[node].read(f"kv/data/{path}")


@pytest.mark.parametrize("node", NODES)
@pytest.mark.parametrize("role", ("puppet", "puppet-server", "puppet-wolf", ""))
def test_certificate_roles_enforce_kv_boundaries(
    vault: tuple[hvac.Client, dict[str, hvac.Client]], node: str, role: str
) -> None:
    """Explicit or automatic role selection cannot grant another node's KV."""
    root, _ = vault
    directory = Path(root.adapter._kwargs["verify"]).parent
    client = hvac.Client(url=root.url, verify=root.adapter._kwargs["verify"])
    login = {
        "name": role,
        "cert_pem": str(directory / f"{node}.pem"),
        "key_pem": str(directory / f"{node}.key"),
    }
    allowed_nodes = {
        "puppet-server": ("docker.home.arpa",),
        "puppet-wolf": ("docker.home.arpa", "proxmox.home.arpa"),
    }
    if role in allowed_nodes and node not in allowed_nodes[role]:
        with pytest.raises(hvac.exceptions.InvalidRequest):
            client.auth.cert.login(**login)
        return
    result = client.auth.cert.login(**login)
    if role:
        assert set(result["auth"]["policies"]) - {"default"} == {role}
    for path, policy in (("puppet", "puppet-server"), ("wolf", "puppet-wolf")):
        if policy in result["auth"]["policies"]:
            assert node in allowed_nodes[policy]
            assert client.read(f"kv/data/{path}")["data"]["data"] == {
                "value": "fixture-secret"
            }
        else:
            with pytest.raises(hvac.exceptions.Forbidden):
                client.read(f"kv/data/{path}")
    if role in allowed_nodes:
        with pytest.raises(hvac.exceptions.Forbidden):
            client.write("pki_int/issue/puppet", common_name=node)


@pytest.mark.parametrize("role", ("puppet-server", "puppet-wolf"))
@pytest.mark.parametrize("mount", ("puppet_ca", "pki_int"))
def test_privileged_login_requires_puppet_ca_and_exact_common_name(
    vault: tuple[hvac.Client, dict[str, hvac.Client]],
    tmp_path: Path,
    role: str,
    mount: str,
) -> None:
    """A privileged SAN or a service-CA certificate cannot impersonate Docker."""
    root, _ = vault
    certificate = root.secrets.pki.generate_certificate(
        "nodes" if mount == "puppet_ca" else "general",
        common_name="complex.home.arpa" if mount == "puppet_ca" else "docker.home.arpa",
        extra_params={"alt_names": "docker.home.arpa"},
        mount_point=mount,
    )["data"]
    cert_file = tmp_path / "cert.pem"
    key_file = tmp_path / "key.pem"
    cert_file.write_text(certificate["certificate"])
    key_file.write_text(certificate["private_key"])
    client = hvac.Client(url=root.url, verify=root.adapter._kwargs["verify"])
    with pytest.raises(hvac.exceptions.InvalidRequest):
        client.auth.cert.login(
            name=role, cert_pem=str(cert_file), key_pem=str(key_file)
        )
