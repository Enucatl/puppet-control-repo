"""Verify private rendering of the copied access stacks."""

import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest


@pytest.fixture
def preparation(tmp_path, monkeypatch):
    """Load the preparation script against inert copied stack fixtures."""
    spec = importlib.util.spec_from_file_location(
        "atlas_access_prepare",
        Path(__file__).parents[1] / "proxmox/atlas-access-prepare.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    subuid = tmp_path / "subuid"
    subuid.write_text("dockremap:100000:65536\nsysbox:165536:65536\n")
    monkeypatch.setattr(module, "SUBUID", subuid)
    monkeypatch.setattr(
        module,
        "DOCKER_SOCKET",
        SimpleNamespace(stat=lambda: SimpleNamespace(st_gid=982)),
    )
    (tmp_path / ".env").write_text("SECRET=keep-me\nDOCKER_DOMAIN=docker.home.arpa\n")
    for stack, directory, filename, text in (
        (
            "traefik",
            "data",
            "config.yml",
            'http:\n  middlewares:\n    default-allowlist:\n      ipAllowList:\n        sourceRange: [10.0.0.0/24, "{{ env \\"IPV6_LAN_SUBNET\\" }}"]\n'
            "tls:\n  stores:\n    default:\n      defaultCertificate:\n        certFile: /certs/docker_fullchain.pem\n        keyFile: /certs/docker_key.pem\n  options:\n    default:\n      clientAuth:\n        clientAuthType: RequireAndVerifyClientCert\n        caFiles: [old-root, old-intermediate]\n",
        ),
        (
            "authelia",
            "config",
            "configuration.yml",
            'access_control:\n  default_policy: deny\n  networks:\n    - name: local\n      networks: [10.0.0.0/24, \'{{ env "IPV6_LAN_SUBNET" }}\']\n  rules:\n    - domain: "*.docker.home.arpa"\n      networks: [local]\n      subject: ["group:admins"]\n      policy: one_factor\n'
            "url: https://auth.docker.home.arpa\nldap: ldaps://freeipa.home.arpa\nsecret: keep-me\n",
        ),
    ):
        root = tmp_path / stack
        (root / directory).mkdir(parents=True)
        (root / directory / filename).write_text(text)
        (root / ".env").write_text("SECRET=keep-me\n")
        (root / "docker-compose.yml").write_text("original-compose")
    (tmp_path / "traefik/data/certs").mkdir()
    source = {
        "traefik": {
            "services": {
                "traefik": {
                    "image": "traefik:old",
                    "healthcheck": {
                        "test": ["CMD", "traefik", "healthcheck", "--ping"]
                    },
                    "read_only": True,
                    "cap_drop": ["ALL"],
                    "deploy": {"resources": {"limits": {"memory": "100M"}}},
                    "volumes": [
                        {
                            "type": "bind",
                            "source": "/old",
                            "target": "/run/mtls-ca/docker-root-ca.crt",
                        },
                        {"type": "bind", "source": "/other", "target": "/keep"},
                    ],
                },
                "socket-proxy": {
                    "image": "proxy@sha256:6806f",
                    "user": "0:988",
                    "environment": {
                        "SOCKET_PROXY_UID": "101000",
                        "SOCKET_PROXY_GID": "101000",
                    },
                },
            }
        },
        "authelia": {
            "services": {
                name: {
                    "image": image,
                    "environment": {"SECRET": "keep-me"},
                    "restart": "always",
                }
                for name, image in [
                    ("authelia", "authelia/authelia:old"),
                    ("postgres", "postgres:old"),
                    ("redis", "redis:old"),
                ]
            }
        },
    }
    calls = []

    def run(arguments, **kwargs):
        """Return resolved Compose privately and record command boundaries."""
        calls.append((arguments, kwargs))
        if arguments[0] == "setfacl":
            return subprocess.CompletedProcess(arguments, 0, "", "")
        stack = Path(arguments[arguments.index("-f") + 1]).parent.name
        return subprocess.CompletedProcess(arguments, 0, json.dumps(source[stack]), "")

    monkeypatch.setattr(module.subprocess, "run", run)
    return module, tmp_path, calls


def test_render_is_private_complete_and_repeatable(preparation, capsys):
    """Keep source hardening and secrets while adapting only Atlas endpoints."""
    module, root, calls = preparation
    module.prepare()
    first = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    module.prepare()
    assert first == {
        path: path.read_bytes() for path in root.rglob("*") if path.is_file()
    }
    assert capsys.readouterr().out == ""
    assert (
        root / ".env"
    ).read_text() == "SECRET=keep-me\nDOCKER_DOMAIN=atlas.home.arpa\n"
    assert "docker.home.arpa" in (root / ".env.pre-atlas").read_text()
    traefik = json.loads((root / "traefik/atlas-compose.json").read_text())["services"]
    assert traefik["traefik"]["image"] == "ghcr.io/enucatl/traefik:latest"
    assert traefik["traefik"]["healthcheck"]["test"] == [
        "CMD",
        "/traefik",
        "healthcheck",
        "--ping",
    ]
    assert traefik["socket-proxy"]["image"] == "proxy@sha256:6806f"
    assert traefik["socket-proxy"]["user"] == "0:982"
    assert traefik["socket-proxy"]["environment"] == {
        "SOCKET_PROXY_UID": "101000",
        "SOCKET_PROXY_GID": "101000",
    }
    assert traefik["traefik"]["read_only"] is True
    assert traefik["traefik"]["cap_drop"] == ["ALL"]
    assert traefik["traefik"]["deploy"]["resources"]["limits"]["memory"] == "100M"
    assert len(traefik["traefik"]["volumes"]) == 2
    assert traefik["traefik"]["volumes"][-1]["read_only"] is True
    dynamic = (root / "traefik/data/config.yml").read_text()
    assert "atlas_fullchain.pem" in dynamic and "atlas_key.pem" in dynamic
    assert "/run/mtls-ca/atlas-root-ca.crt" in dynamic and "old-root" not in dynamic
    assert "RequireAndVerifyClientCert" in dynamic
    authelia = json.loads((root / "authelia/atlas-compose.json").read_text())[
        "services"
    ]
    assert authelia["authelia"]["environment"] == {
        "SECRET": "keep-me",
        "AUTHELIA_AUTHENTICATION_BACKEND_LDAP_ADDRESS": "ldaps://ipa.home.arpa",
    }
    for service in authelia.values():
        assert "@sha256:" in service["image"] and service["restart"] == "always"
    assert (
        "https://auth.atlas.home.arpa"
        in (root / "authelia/config/configuration.yml").read_text()
    )
    assert (
        "ldaps://ipa.home.arpa"
        in (root / "authelia/config/configuration.yml").read_text()
    )
    for path in first:
        if (
            path.name not in (".env", "docker-compose.yml", "subuid")
            or path == root / ".env"
        ):
            assert path.stat().st_mode & 0o777 == 0o600
    assert (root / "traefik/docker-compose.yml").read_text() == "original-compose"
    assert all(
        options == {"capture_output": True, "text": True} for _, options in calls
    )
    assert all("keep-me" not in " ".join(arguments) for arguments, _ in calls)


def test_compose_error_does_not_disclose_secrets(preparation, monkeypatch):
    """Never put subprocess diagnostics into an exception or console output."""
    module, _, _ = preparation
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 1, "private-value", "private-token"
        ),
    )
    with pytest.raises(RuntimeError, match="diagnostic output withheld") as error:
        module.compose("authelia")
    assert "private" not in str(error.value)


def test_local_ula_rules_match_router_and_preserve_policies(preparation):
    """Allow approved ULA segments once while keeping existing access controls."""
    import yaml

    module, root, _ = preparation
    router = yaml.safe_load(
        (Path(__file__).parents[1] / "provisioning/host_vars/router.yml").read_text()
    )
    segments = (
        0,
        int(router["trusted_wifi_vlan"]),
        int(router["cortex_vlan"]),
        int(router["wg_subnet"].split(".")[2]),
    )
    expected = [f"{router['ula_prefix']}:{segment:x}::/64" for segment in segments]
    assert list(module.LOCAL_ULA_NETWORKS) == expected
    module.prepare()
    module.prepare()
    dynamic = yaml.safe_load((root / "traefik/data/config.yml").read_text())
    ranges = dynamic["http"]["middlewares"]["default-allowlist"]["ipAllowList"][
        "sourceRange"
    ]
    access = yaml.safe_load((root / "authelia/config/configuration.yml").read_text())[
        "access_control"
    ]
    local = access["networks"][0]["networks"]
    for networks in (ranges, local):
        assert networks[:2] == ["10.0.0.0/24", '{{ env "IPV6_LAN_SUBNET" }}']
        assert networks[2:] == expected
    assert access["default_policy"] == "deny"
    assert access["rules"] == [
        {
            "domain": "*.atlas.home.arpa",
            "networks": ["local"],
            "subject": ["group:admins"],
            "policy": "one_factor",
        }
    ]


def test_owner_preserved_and_runtime_acl_is_scoped(preparation, monkeypatch):
    """Keep FreeIPA ownership and give mapped service UID only named file access."""
    module, root, calls = preparation
    ownership = []
    directory_ownership = []
    monkeypatch.setattr(
        module.os,
        "chown",
        lambda path, uid, gid: directory_ownership.append((path, uid, gid)),
    )
    monkeypatch.setattr(
        module.os, "fchown", lambda fd, uid, gid: ownership.append((uid, gid))
    )
    owner = (root / "authelia/config/configuration.yml").stat()
    module.prepare()
    assert ownership and set(ownership) == {(owner.st_uid, owner.st_gid)}
    acl_commands = [arguments for arguments, _ in calls if arguments[0] == "setfacl"]
    certs = root / "traefik/data/certs"
    stack_owner = (root / "traefik").stat()
    assert directory_ownership == [(certs, stack_owner.st_uid, stack_owner.st_gid)]
    assert acl_commands == [
        ["setfacl", "-m", "u:101000:r-x", "--", str(certs)],
        [
            "setfacl",
            "-m",
            "u:101000:r--",
            "--",
            str(root / "traefik/data/config.yml"),
            str(root / "authelia/config/configuration.yml"),
            str(certs / "atlas_fullchain.pem"),
            str(certs / "atlas_key.pem"),
        ],
    ]
    assert all(
        "atlas-compose.json" not in " ".join(command)
        and ".env" not in " ".join(command)
        for command in acl_commands
    )
    assert (root / "authelia/atlas-compose.json").stat().st_mode & 0o777 == 0o600


def test_unexpected_user_namespace_fails_before_writes(preparation):
    """Reject UID mapping changes before writing secrets or granting file access."""
    module, root, calls = preparation
    module.SUBUID.write_text("dockremap:200000:65536\n")
    before = (root / ".env").read_bytes()
    with pytest.raises(RuntimeError, match="subordinate UID range"):
        module.prepare()
    assert (root / ".env").read_bytes() == before
    assert not calls


def test_certificate_acls_survive_renewal():
    """Subscribe each certificate ACL to Vault replacement without broad modes."""
    import yaml

    node = Path(__file__).parents[1] / "data/nodes/atlas.home.arpa.yaml"
    acls = yaml.safe_load(node.read_text())["profile::common::acls::posix_acls"]
    certs = "/opt/docker/traefik/data/certs"
    assert acls[certs]["permission"] == ["user:101000:r-x", "mask::r-x"]
    for leaf in ("atlas_fullchain.pem", "atlas_key.pem"):
        assert acls[f"{certs}/{leaf}"] == {
            "action": "set",
            "permission": ["user:101000:r--", "mask::r--"],
            "subscribe": "Vault_cert[atlas]",
        }


def test_compose_includes_requested_runtime_override(preparation):
    """Load the actual merged source definition when a stack uses an override."""
    module, root, calls = preparation
    module.compose("traefik", override=True)
    command = calls[-1][0]
    assert command.count("-f") == 2
    assert command[command.index("-f") + 1] == str(root / "traefik/docker-compose.yml")
    assert command[-4:] == [
        "-f",
        str(root / "traefik/docker-compose.override.yml"),
        "config",
        "--format=json",
    ]


def test_real_compose_roundtrip_preserves_literal_dollars(tmp_path):
    """Exercise Compose's actual second interpolation without a Docker engine."""
    spec = importlib.util.spec_from_file_location(
        "atlas_access_roundtrip",
        Path(__file__).parents[1] / "proxmox/atlas-access-prepare.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = tmp_path / "atlas-compose.json"
    expected = {
        "image": "busybox:1",
        "environment": {"EXAMPLE": "prefix-$UNSET-${UNSET}-$$-suffix"},
        "command": ["sh", "-c", 'printf "%s" "$EXAMPLE"; echo ${OTHER:-default}'],
        "labels": {"example.redirect": "https://${1}/$2/$$"},
    }
    source = tmp_path / "source-compose.json"
    source.write_text(
        json.dumps({"services": {"example": expected}}).replace("$", "$$")
    )

    def resolve(compose_path):
        """Ask real Compose for canonical JSON without connecting to an engine."""
        result = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                "/dev/null",
                "--project-name",
                "literal-dollar-check",
                "-f",
                str(compose_path),
                "config",
                "--format=json",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)

    canonical_source = resolve(source)
    module.write_compose(path, canonical_source)
    assert resolve(path) == canonical_source
    # Compose's canonical output itself doubles literal dollars for re-reading.
    for key, value in expected.items():
        escaped = json.loads(json.dumps(value).replace("$", "$$"))
        assert canonical_source["services"]["example"][key] == escaped
    assert path.stat().st_mode & 0o777 == 0o600
