"""Validate the opt-in production model without contacting a Docker daemon."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import re

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def compose_model():
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker Compose CLI is not installed")
    environment = {
        "PATH": os.environ["PATH"],
        "DOCKER_HOST": "unix:///var/run/docker.sock",
        "DOCKER_DOMAIN": "example.invalid",
    }
    version = subprocess.run(
        [docker, "compose", "version", "--short"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if version.returncode:
        pytest.skip("Docker Compose plugin is not installed")
    result = subprocess.run(
        [
            docker,
            "compose",
            "--env-file",
            "/dev/null",
            "-f",
            str(ROOT / "docker/docker-compose.yml"),
            "--profile",
            "openvox",
            "config",
            "--format",
            "json",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_openvox_activation_is_explicit_and_keeps_the_tls_boundary(compose_model):
    service = compose_model["services"]["openvox"]
    assert service["profiles"] == ["openvox"]
    assert not service.get("depends_on")
    assert service["hostname"] == "openvox.home.arpa"
    assert service["user"] == "999:999"
    assert service["read_only"] is True
    assert "ALL" in service["cap_drop"]
    assert set(service["networks"]) == {"infra"}
    assert not service.get("labels")
    assert service["ports"][0]["target"] == 8140
    assert service["environment"]["VAULT_ADDR"] == "https://vault.home.arpa:8200"
    assert service["environment"]["VAULT_CACERT"] == "/trust/root.pem"
    for option in ("NO_PROXY", "no_proxy"):
        assert set(service["environment"][option].split(",")) == {
            "vault.home.arpa",
            "openvox.home.arpa",
            "127.0.0.1",
            "localhost",
        }
    assert "-Djdk.tls.namedGroups=X25519MLKEM768" in service["environment"]["JAVA_ARGS"]


def test_deployed_hiera_uses_the_mounted_vault_trust(compose_model):
    """Keep Atlas lookups on its endpoint without redirecting the legacy compiler."""
    ruby = shutil.which("ruby")
    if ruby is None:
        pytest.skip("Ruby is required to parse the deployed Hiera configuration")
    result = subprocess.run(
        [
            ruby,
            "-ryaml",
            "-rjson",
            "-e",
            "puts JSON.generate(ARGV.map { |path| YAML.safe_load_file(path) })",
            str(ROOT / "docker/openvox/infra-hiera.yaml"),
            str(ROOT / "hiera.yaml"),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    deployed, legacy = json.loads(result.stdout)
    backend = next(
        level
        for level in deployed["hierarchy"]
        if level.get("data_hash") == "vault_hiera_hash"
    )
    environment = compose_model["services"]["openvox"]["environment"]
    assert backend["options"]["ca_trust"] == environment["VAULT_CACERT"]
    assert backend["options"]["fail_hard"] is True
    assert backend["uris"] == [environment["VAULT_ADDR"] + "/v1/kv/data/puppet"]
    legacy_backend = next(
        level
        for level in legacy["hierarchy"]
        if level.get("data_hash") == "vault_hiera_hash"
    )
    assert legacy_backend["uris"] == ["https://hcv.home.arpa:8200/v1/kv/data/puppet"]
    legacy_backend["uris"] = backend["uris"]
    assert deployed == legacy


def test_openvox_state_cannot_be_replaced_by_implicit_empty_volumes(compose_model):
    service = compose_model["services"]["openvox"]
    mounts = {mount["target"]: mount for mount in service["volumes"]}
    expected = {
        "/etc/puppetlabs/puppet/ssl",
        "/etc/puppetlabs/puppetserver/ca",
        "/etc/puppetlabs/code",
        "/opt/puppetlabs/server/data/puppetserver/server_data",
        "/opt/puppetlabs/server/data/puppetserver/jruby-gems",
        "/git",
        "/var/cache/r10k",
        "/trust",
    }
    assert {
        m["target"] for m in service["volumes"] if m["type"] == "volume"
    } == expected
    for target in expected:
        volume = compose_model["volumes"][mounts[target]["source"]]
        assert volume["external"] is True
        assert volume["name"].startswith("infra_openvox_")
    assert mounts["/trust"]["read_only"] is True
    # Compose versions can omit false-valued fields from normalized JSON.
    assert all(
        m["bind"].get("create_host_path", False) is False
        for m in service["volumes"]
        if m["type"] == "bind"
    )
    assert not any("docker.sock" in m.get("source", "") for m in service["volumes"])


def test_start_fails_before_launch_when_state_is_missing():
    result = subprocess.run(
        ["bash", str(ROOT / "docker/openvox/infra-start.sh")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "Restore established OpenVox state before startup; missing:" in result.stderr


def test_published_control_repo_is_read_only_with_writable_deployment_status(
    compose_model,
):
    """Expose public control code without allowing deployment to modify its source."""
    mounts = {
        mount["target"]: mount
        for mount in compose_model["services"]["openvox"]["volumes"]
    }
    repository = mounts["/git/control.git"]
    assert repository["type"] == "bind"
    assert repository["source"] == "/opt/git/openvox/control.git"
    assert repository["read_only"] is True
    assert repository["bind"].get("create_host_path", False) is False
    assert mounts["/git"]["type"] == "volume"
    assert mounts["/git"]["source"] == "openvox_git"
    assert mounts["/git"].get("read_only", False) is False


def test_r10k_reads_foreign_owned_repo_using_file_transport_and_scoped_trust():
    """Avoid local clone hard links and trust only the mounted control repository."""
    configuration = (ROOT / "docker/openvox/infra-r10k.yaml").read_text()
    assert re.findall(r"remote:\s*'([^']+)'", configuration) == [
        "file:///git/control.git"
    ]
    dockerfile = (ROOT / "docker/openvox/infra.Dockerfile").read_text()
    assert re.findall(
        r"git config --system --add safe\.directory (\S+)", dockerfile
    ) == ["/git/control.git"]


def test_vault_cutover_is_a_separate_pinned_override(compose_model):
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "-f",
            str(ROOT / "docker/docker-compose.yml"),
            "-f",
            str(ROOT / "docker/openvox/infra-vault.override.yaml"),
            "config",
            "--format",
            "json",
        ],
        env={"PATH": os.environ["PATH"], "DOCKER_DOMAIN": "example.invalid"},
        capture_output=True,
        text=True,
        check=True,
    )
    model = json.loads(result.stdout)
    services = model["services"]
    vault = services["vault"]
    assert compose_model["services"]["vault"]["image"] == "hashicorp/vault:latest"
    assert vault["image"] == (
        "hashicorp/vault@sha256:"
        "e0acd3c681bdf57f002d7214e5d050b876e1704678005c6b0406dd96a65be919"
    )
    assert vault["command"] == [
        "vault",
        "server",
        "-config=/vault/config/infra-vault.hcl",
    ]
    assert vault["environment"]["VAULT_ADDR"] == "https://vault.home.arpa:8200"
    assert set(vault["networks"]) == {"infra"}
    assert vault["networks"]["infra"]["aliases"] == ["vault.home.arpa"]
    assert (
        services["vault-unsealer"]["environment"]["VAULT_ADDR"]
        == (vault["environment"]["VAULT_ADDR"])
    )
    assert (
        'api_addr = "https://vault.home.arpa:8200"'
        in (ROOT / "docker/openvox/infra-vault.hcl").read_text()
    )
    mounts = {mount["target"]: mount for mount in vault["volumes"]}
    assert mounts["/vault/file"]["source"] == "vault_data"
    assert mounts["/vault/logs"]["source"] == "vault_logs"
    assert mounts["/certificates"]["source"] == "certificates"
    assert mounts["/certificates"]["read_only"] is True
    assert mounts["/vault/config/infra-vault.hcl"]["read_only"] is True
    assert "/vault/config" not in mounts
    for name in ("vault_data", "vault_logs", "certificates", "vault_unseal"):
        assert model["volumes"][name] == {
            "external": True,
            "name": f"infra_{name}",
        }
        assert not compose_model["volumes"][name].get("external", False)
    assert services["vault-unsealer"]["command"] == ["/scripts/unseal.sh"]
    assert set(services["vault-unsealer"]["networks"]) == {"infra"}
