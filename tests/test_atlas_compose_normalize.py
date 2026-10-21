"""Verify ordinary Compose keeps Atlas adaptations without frozen runtime files."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest
import yaml


@pytest.fixture
def normalizer(tmp_path, monkeypatch):
    """Load the migration against isolated source repositories."""
    spec = importlib.util.spec_from_file_location(
        "atlas_compose_normalize",
        Path(__file__).parents[1] / "proxmox/atlas-compose-normalize.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    (tmp_path / ".env").write_text("DOCKER_DOMAIN=atlas.home.arpa\nDOMAIN=home.arpa\n")
    monkeypatch.delenv("COMPOSE_ENV_FILES", raising=False)
    monkeypatch.delenv("COMPOSE_DISABLE_ENV_FILE", raising=False)
    return module, tmp_path


def config(directory, *flags):
    """Resolve the same ordinary Compose command a human would use."""
    result = subprocess.run(
        ["docker", "compose", *flags, "config", "--format=json"],
        cwd=directory,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_native_override_preserves_source_changes_and_secret_values(normalizer):
    """Keep build/image updates live while copying only explicit native differences."""
    module, root = normalizer
    directory = root / "grafana-loki"
    directory.mkdir()
    (directory / ".env").write_text("SECRET=sensitive-docker.home.arpa\n")
    source = {
        "services": {
            "grafana": {
                "image": "grafana/grafana:latest",
                "environment": {"SECRET": "${SECRET}", "DOMAIN": "${DOMAIN}"},
            },
            "loki": {"image": "grafana/loki:latest"},
        },
    }
    path = directory / "docker-compose.yml"
    path.write_text(yaml.safe_dump(source))
    module.normalize("grafana-loki")
    before = {p: p.read_bytes() for p in directory.iterdir()}
    module.normalize("grafana-loki")
    assert before == {p: p.read_bytes() for p in directory.iterdir()}
    resolved = config(directory)["services"]
    assert (
        resolved["grafana"]["image"]
        == "grafana/grafana-image-tags:13.3.0-39395647570-amd64"
    )
    assert resolved["grafana"]["environment"] == {
        "SECRET": "sensitive-docker.home.arpa",
        "DOMAIN": "home.arpa",
    }
    assert resolved["loki"]["ports"][0]["host_ip"] == "127.0.0.1"
    assert "sensitive" not in (directory / "docker-compose.override.yml").read_text()
    assert not (directory / "atlas-compose.json").exists()
    source["services"]["loki"]["image"] = "grafana/loki:next"
    source["services"]["grafana"]["environment"]["NEW_OPTION"] = "propagates"
    path.write_text(yaml.safe_dump(source))
    resolved = config(directory)["services"]
    assert resolved["loki"]["image"] == "grafana/loki:next"
    assert resolved["grafana"]["environment"]["NEW_OPTION"] == "propagates"


def test_backrest_restores_only_documents_schedule(normalizer):
    """Keep repository credentials and other plans while reenabling Documents."""
    module, root = normalizer
    directory = root / "backrest"
    (directory / "backrest").mkdir(parents=True)
    (directory / "docker-compose.yml").write_text(
        "services:\n  backrest:\n    image: backrest:test\n"
    )
    template = directory / "backrest/config.template.json"
    template.write_text(
        json.dumps(
            {
                "repos": [{"password": "__BACKREST_REPO_PASSWORD__"}],
                "plans": [
                    {"id": "docker", "schedule": {"cron": "0 8 * * 1"}},
                    {"id": "documents", "schedule": {"disabled": True}},
                ],
            }
        )
    )
    module.normalize("backrest")
    config = json.loads(template.read_text())
    assert config["repos"][0]["password"] == "__BACKREST_REPO_PASSWORD__"
    assert config["plans"][0]["schedule"] == {"cron": "0 8 * * 1"}
    assert config["plans"][1]["schedule"] == {
        "cron": "0 0 * * 1",
        "clock": "CLOCK_LOCAL",
    }
    assert template.stat().st_mode & 0o777 == 0o644


def test_beszel_inventory_uses_migrated_dns_without_changing_access(normalizer):
    """Keep system IDs and user access while removing DHCP-dependent targets."""
    module, root = normalizer
    directory = root / "beszel"
    directory.mkdir()
    (directory / "docker-compose.yml").write_text(
        "services:\n  beszel:\n    image: beszel:test\n"
    )
    systems = [
        {
            "id": "stable",
            "name": "forbearance.home.arpa",
            "host": "10.0.0.136",
            "users": ["user-id"],
        },
        {"name": "complex.home.arpa", "host": "10.0.40.133"},
        {"name": "docker.home.arpa", "host": "docker.home.arpa"},
        {"name": "proxmox.home.arpa", "host": "10.0.0.10"},
    ]
    path = directory / "config.yml"
    path.write_text(yaml.safe_dump({"systems": systems}))
    alerts = directory / "alerts.yml"
    alerts.write_text(
        yaml.safe_dump(
            {
                "user": "user-id",
                "systems": {"docker.home.arpa": {"CPU": {"threshold": 90}}},
            }
        )
    )
    module.normalize("beszel")
    migrated = yaml.safe_load(path.read_text())["systems"]
    assert migrated[:4] == systems
    assert migrated[4] == {
        "name": "aperture.home.arpa",
        "host": "aperture.home.arpa",
        "users": ["user-id"],
    }
    assert migrated[5]["host"] == "cognition.home.arpa"
    assert migrated[6]["host"] == "atlas.home.arpa"
    assert yaml.safe_load(alerts.read_text()) == {
        "user": "user-id",
        "systems": {"atlas.home.arpa": {"CPU": {"threshold": 90}}},
    }
    before = path.read_bytes()
    module.normalize("beszel")
    assert path.read_bytes() == before


def test_airflow_retains_ca_and_only_downloader_archive(normalizer):
    """Replace inherited archive mounts without removing the runner trust bind."""
    module, root = normalizer
    directory = root / "airflow"
    directory.mkdir()
    services = {}
    for name in ("exam", "download-zanzara"):
        services[name] = {
            "image": "runner:latest",
            "profiles": ["runner"],
            "environment": {"VAULT_ADDR": "https://vault.docker.home.arpa:8200"},
            "volumes": [
                "/etc/ssl/certs:/etc/ssl/certs:ro",
                "/scratch/archive:/scratch/archive",
            ],
        }
    (directory / "docker-compose.yml").write_text(
        yaml.safe_dump({"services": services})
    )
    module.normalize("airflow")
    resolved = config(directory, "--profile", "runner")["services"]
    for service in resolved.values():
        assert service["environment"]["VAULT_ADDR"] == "https://vault.home.arpa:8200"
        assert service["volumes"][0]["target"] == "/etc/ssl/certs"
    assert len(resolved["exam"]["volumes"]) == 1
    assert len(resolved["download-zanzara"]["volumes"]) == 2


def test_bills_environment_preserves_smtp_identity_and_has_no_gate(normalizer):
    """Normalize only browser endpoints; keep SMTP identity and source images."""
    module, root = normalizer
    directory = root / "send-bills"
    directory.mkdir()
    (directory / ".env").write_text(
        "DJANGO_EMAIL_HOST=docker.home.arpa\nSECRET=sensitive-docker.home.arpa\n"
        "DJANGO_ALLOWED_HOSTS=bills.${DOCKER_DOMAIN}\n"
        "DOCKER_DOMAIN=docker.home.arpa\n"
    )
    (directory / "docker-compose.yml").write_text(
        "services:\n  scheduler:\n    image: ghcr.io/enucatl/bills:latest\n"
        "    environment:\n      DJANGO_EMAIL_HOST: ${DJANGO_EMAIL_HOST}\n"
        "      SECRET: ${SECRET}\n      DJANGO_ALLOWED_HOSTS: ${DJANGO_ALLOWED_HOSTS}\n"
    )
    module.normalize("send-bills")
    scheduler = config(directory)["services"]["scheduler"]
    assert scheduler["image"] == "ghcr.io/enucatl/bills:latest"
    assert "profiles" not in scheduler
    assert scheduler["environment"]["DJANGO_EMAIL_HOST"] == "docker.home.arpa"
    assert scheduler["environment"]["SECRET"] == "sensitive-docker.home.arpa"
    assert scheduler["environment"]["DJANGO_ALLOWED_HOSTS"] == "bills.atlas.home.arpa"
