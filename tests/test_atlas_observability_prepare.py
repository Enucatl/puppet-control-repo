"""Exercise observability preparation without running Docker or exposing secrets."""

import copy
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


@pytest.fixture
def observability(tmp_path, monkeypatch):
    """Prepare copied stack fixtures and inert subprocess boundaries."""
    spec = importlib.util.spec_from_file_location(
        "atlas_observability_prepare",
        Path(__file__).parents[1] / "proxmox/atlas-observability-prepare.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.access, "ROOT", tmp_path)
    subuid = tmp_path / "subuid"
    subuid.write_text("dockremap:100000:65536\n")
    monkeypatch.setattr(module.access, "SUBUID", subuid)
    (tmp_path / ".env").write_text("DOCKER_DOMAIN=atlas.home.arpa\nSECRET=untouched\n")
    grafana = tmp_path / "grafana-loki/grafana"
    grafana.mkdir(parents=True)
    (grafana / "grafana.ini").write_text(
        "[server]\ndomain = grafana.docker.home.arpa\nroot_url = https://%(domain)s/\n"
        "[auth.generic_oauth]\nauth_url = https://authelia.docker.home.arpa/auth\n"
        "token_url = https://authelia.docker.home.arpa/token\n"
        "api_url = https://authelia.docker.home.arpa/userinfo\n"
        "client_secret = literal-docker.home.arpa-secret\n"
    )
    beszel = tmp_path / "beszel"
    beszel.mkdir()
    (beszel / "config.yml").write_text(
        "systems:\n  - name: docker.home.arpa\n    host: 172.17.96.1\n"
        'secret: {{ secret "/run/secrets/test" }}\n'
    )

    def service():
        """Represent source settings that rendering must retain."""
        return {
            "image": "old:latest",
            "read_only": True,
            "cap_drop": ["ALL"],
            "environment": {"SECRET": "literal-docker.home.arpa-secret"},
            "labels": {
                "traefik.http.routers.test.rule": "Host(`test.docker.home.arpa`)",
                "custom": "docker.home.arpa",
            },
            "volumes": [{"type": "volume", "source": "data", "target": "/data"}],
        }

    configs = {
        "grafana-loki": {
            "services": {"grafana": service(), "loki": service()},
            "networks": {"ais_grafana": {"external": True}},
            "volumes": {"data": {}},
        },
        "beszel": {
            "services": {"beszel": service(), "alerts-init": {"build": "."}},
            "volumes": {"data": {}},
        },
    }
    monkeypatch.setattr(
        module.access, "compose", lambda name: copy.deepcopy(configs[name])
    )
    calls = []

    def run(arguments, **kwargs):
        """Record permission commands without changing host ACLs."""
        calls.append((arguments, kwargs))
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(module.subprocess, "run", run)
    return module, tmp_path, calls


def test_pinned_render_retains_settings_and_secrets(observability, capsys):
    """Adapt only named endpoints and suppress the staged alert initializer."""
    module, root, calls = observability
    module.prepare()
    first = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    module.prepare()
    assert first == {
        path: path.read_bytes() for path in root.rglob("*") if path.is_file()
    }
    grafana = (root / "grafana-loki/grafana/grafana.ini").read_text()
    assert "domain = grafana.atlas.home.arpa" in grafana
    assert "https://authelia.atlas.home.arpa/token" in grafana
    assert "client_secret = literal-docker.home.arpa-secret" in grafana
    beszel = (root / "beszel/config.yml").read_text()
    assert "host: docker.home.arpa" in beszel and "172.17.96.1" not in beszel
    assert '{{ secret "/run/secrets/test" }}' in beszel
    for stack in ("grafana-loki", "beszel"):
        path = root / stack / "atlas-compose.json"
        config = json.loads(path.read_text())
        assert path.stat().st_mode & 0o777 == 0o600
        assert "alerts-init" not in config["services"]
        for name, service in config["services"].items():
            assert service["image"] == module.IMAGES[name]
            assert service["read_only"] and service["cap_drop"] == ["ALL"]
            assert service["environment"]["SECRET"] == "literal-docker.home.arpa-secret"
            assert service["labels"]["custom"] == "docker.home.arpa"
            assert (
                "test.atlas.home.arpa"
                in service["labels"]["traefik.http.routers.test.rule"]
            )
    assert (
        json.loads((root / "beszel/atlas-compose.json").read_text())["services"][
            "beszel"
        ]["environment"]["APP_URL"]
        == "https://beszel.atlas.home.arpa"
    )
    assert json.loads((root / "grafana-loki/atlas-compose.json").read_text())[
        "networks"
    ]["ais_grafana"]["external"]
    assert {command[2] for command, _ in calls} == {"u:100472:r--", "u:100000:r--"}
    assert capsys.readouterr().out == ""
    assert all(
        options == {"capture_output": True, "text": True} for _, options in calls
    )


def test_invalid_mapping_fails_before_configuration_changes(observability):
    """Reject a different user namespace before writing configuration."""
    module, root, calls = observability
    module.access.SUBUID.write_text("dockremap:200000:65536\n")
    with pytest.raises(RuntimeError, match="subordinate UID"):
        module.prepare()
    assert not calls
    assert not (root / "beszel/atlas-compose.json").exists()
