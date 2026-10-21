"""Check ordinary Compose entrypoints retain native identities and authority data."""

import json
import os
from pathlib import Path
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
    """Keep native OpenVox/Vault active while old IPA, Wolf and flow definitions opt out."""
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
    assert services["vault"]["command"] == [
        "vault",
        "server",
        "-config=/vault/config/infra-vault.hcl",
    ]
    volumes = {mount["target"]: mount for mount in services["vault"]["volumes"]}
    assert volumes["/vault/file"]["source"] == "vault_data"
    assert volumes["/certificates"]["read_only"] is True
    assert volumes["/vault/config/infra-vault.hcl"]["source"] == str(
        ROOT / "docker/openvox/infra-vault.hcl"
    )
    for name in (
        "vault_data",
        "vault_logs",
        "certificates",
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
    assert public["volume"]["subpath"] == "public"


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
    assert service["hostname"] == "ipa.home.arpa"
    assert service["runtime"] == "sysbox-runc"
    assert config["volumes"]["ipa_data"] == {"name": "infra_ipa_data", "external": True}
    assert "--cacert /bootstrap/root.pem" in service["healthcheck"]["test"][1]
