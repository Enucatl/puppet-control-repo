"""Check ordinary Compose entrypoints retain native identities and authority data."""

import json
import os
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).parents[1]


def resolve(path: Path) -> dict:
    """Resolve source configuration without local env files or container changes."""
    environment = os.environ.copy()
    environment.pop("COMPOSE_ENV_FILES", None)
    environment.pop("COMPOSE_DISABLE_ENV_FILE", None)
    environment["DOCKER_DOMAIN"] = "atlas.home.arpa"
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "-f",
            str(path),
            "config",
            "--format=json",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_root_defaults_preserve_native_authorities() -> None:
    """Keep native authorities active and standalone Wolf/flow definitions opted out."""
    config = resolve(ROOT / "compose.yaml")
    assert config["name"] == "infra"
    assert set(config["services"]) == {
        "openvox",
        "vault",
        "vault-unsealer",
        "apt-cacher-ng",
    }
    services = config["services"]
    assert services["openvox"]["image"] == "infra-openvox:24001"
    assert services["openvox"]["command"] == [
        "/opt/puppetlabs/server/apps/puppetserver/cli/apps/foreground"
    ]
    assert "freeipa_data" not in config["volumes"]
    assert services["vault"]["command"] == [
        "vault",
        "server",
        "-config=/vault/config/infra-vault.hcl",
    ]
    volumes = {mount["target"]: mount for mount in services["vault"]["volumes"]}
    assert volumes["/vault/file"]["source"] == "vault_data"
    assert volumes["/certificates"]["type"] == "bind"
    assert volumes["/certificates"]["source"] == str(ROOT / "docker/vault/secrets")
    assert volumes["/certificates"]["read_only"] is True
    assert volumes["/vault/config/infra-vault.hcl"]["source"] == str(
        ROOT / "docker/openvox/infra-vault.hcl"
    )
    for name in (
        "vault_data",
        "vault_logs",
        "vault_unseal",
        "openvox_ssl",
        "openvox_ca",
        "openvox_code",
    ):
        assert config["volumes"][name] == {"name": f"infra_{name}", "external": True}
    unsealer = services["vault-unsealer"]
    assert unsealer["environment"]["VAULT_PINNED_PUBKEY"] == "/public/vault.pub.pem"
    assert unsealer["healthcheck"]["test"] == [
        "CMD",
        "/scripts/unseal.sh",
        "--healthcheck",
    ]
    public = next(
        mount for mount in unsealer["volumes"] if mount["target"] == "/public"
    )
    assert public["read_only"] is True
    assert public["type"] == "bind"
    assert public["source"] == str(ROOT / "docker/vault/secrets/public")


def test_wolf_and_flow_standalone_sources_preserve_runtime_identity(
    tmp_path: Path,
) -> None:
    """Resolve mounted production paths without inheriting retired profiles/networks."""
    for name in ("wolf", "goflow2"):
        path = tmp_path / f"{name}.yaml"
        path.write_text(
            (ROOT / f"docker/{name}/compose.yaml")
            .read_text()
            .replace("/opt/docker/puppet-control-repo", str(ROOT))
        )
        config = resolve(path)
        assert config["name"] == name
        assert set(config["services"]) == {name}
        service = config["services"][name]
        assert "profiles" not in service
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        if name == "wolf":
            assert service["container_name"] == "infra-wolf-1"
            assert service["build"]["context"] == str(ROOT / "docker/wolf")
            assert service["environment"]["WOLF_GUEST_HOST"] == "cognition.home.arpa"
            assert service["environment"]["WOLF_VM_ID"] == "220"
            assert (
                service["labels"]["traefik.http.routers.wolf.rule"]
                == "Host(`wolf.atlas.home.arpa`)"
            )
            assert config["volumes"]["wolf_state"] == {
                "name": "infra_wolf_state",
                "external": True,
            }
            assert set(service["networks"]) == {"traefik_proxy"}
        else:
            assert service["image"] == "netsampler/goflow2:latest"
            assert set(service["networks"]) == {"default"}
            assert service["ports"][0]["published"] == "2055"
            assert service["ports"][0]["protocol"] == "udp"


def test_freeipa_ordinary_entrypoint_preserves_native_service() -> None:
    """Alias the accepted IPA source, including its persistent identity and real check."""
    config = resolve(ROOT / "docker/freeipa/compose.yaml")
    assert config["name"] == "infra"
    assert set(config["services"]) == {"ipa"}
    service = config["services"]["ipa"]
    assert re.fullmatch(
        r"ghcr\.io/enucatl/freeipa-server@sha256:[0-9a-f]{64}", service["image"]
    )
    assert "build" not in service
    assert service["entrypoint"] == ["/usr/local/sbin/init"]
    assert service["hostname"] == "ipa.home.arpa"
    assert service["runtime"] == "sysbox-runc"
    assert config["volumes"]["ipa_data"] == {"name": "infra_ipa_data", "external": True}
    assert "--cacert /etc/ipa/ca.crt" in service["healthcheck"]["test"][1]
    mounts = {mount["target"]: mount for mount in service["volumes"]}
    assert len(service["volumes"]) == 3
    assert service["volumes"][0] == mounts["/data"]
    assert mounts["/data"]["type"] == "volume"
    assert mounts["/data"]["source"] == "ipa_data"
    assert mounts["/bootstrap"]["source"] == str(ROOT / "docker/freeipa/secrets")
    assert mounts["/bootstrap"]["read_only"] is True
    assert mounts["/data/ipa-server-install-options"]["source"] == str(
        ROOT / "docker/freeipa/secrets/ipa-server-install-options"
    )
    assert mounts["/data/ipa-server-install-options"]["read_only"] is True
    for target in ("/bootstrap", "/data/ipa-server-install-options"):
        assert mounts[target]["type"] == "bind"
        assert mounts[target]["bind"]["create_host_path"] is False
    options = service["command"]
    assert "--no-pkinit" in options
    assert "--ca-cert-file=/bootstrap/ca.pem" in options
    for name in ("http", "dirsrv"):
        for filename in ("fullchain.pem", "key.pem"):
            assert f"--{name}-cert-file=/bootstrap/{filename}" in options
    assert "--external-ca" not in options
    assert not any(
        option.startswith(("--admin-password", "--ds-password")) for option in options
    )
    assert sorted((port["target"], port["protocol"]) for port in service["ports"]) == [
        (88, "tcp"),
        (88, "udp"),
        (389, "tcp"),
        (464, "tcp"),
        (464, "udp"),
        (636, "tcp"),
    ]
    assert set(service["networks"]) == {"infra", "traefik_proxy"}
    proxy = config["networks"]["traefik_proxy"]
    assert proxy["name"] == "traefik_proxy"
    assert proxy["external"] is True
    assert service["labels"] == {
        "traefik.enable": "true",
        "traefik.docker.network": "traefik_proxy",
        "traefik.tcp.routers.freeipa-secure.entrypoints": "https",
        "traefik.tcp.routers.freeipa-secure.rule": "HostSNI(`ipa.home.arpa`)",
        "traefik.tcp.routers.freeipa-secure.tls": "true",
        "traefik.tcp.routers.freeipa-secure.tls.passthrough": "true",
        "traefik.tcp.services.freeipa-secure.loadbalancer.server.port": "443",
    }
