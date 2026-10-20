"""Verify staged stateful rendering preserves secrets and defers external jobs."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def stateful(tmp_path, monkeypatch):
    """Load the renderer with copied stack and image fixtures."""
    spec = importlib.util.spec_from_file_location(
        "atlas_stateful_prepare",
        Path(__file__).parents[1] / "proxmox/atlas-stateful-prepare.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.access, "ROOT", tmp_path)
    (tmp_path / ".env").write_text("DOCKER_DOMAIN=atlas.home.arpa\n")
    service_names = {
        "paperless-ai": ["webserver", "db"],
        "browser-use": ["controller", "db"],
        "crabberbot": ["crabberbot", "cloudflared"],
        "send-bills": ["bills", "scheduler", "db"],
    }
    configs = {}
    images = {}
    for stack, names in service_names.items():
        (tmp_path / stack).mkdir()
        images[stack] = {name: "sha256:" + "a" * 64 for name in names}
        configs[stack] = {
            "services": {
                name: {
                    "image": "source:latest",
                    "build": {"context": str(tmp_path / stack)},
                    "read_only": True,
                    "cap_drop": ["ALL"],
                    "environment": {
                        "DJANGO_EMAIL_HOST": "docker.home.arpa",
                        "SECRET": "sensitive-docker.home.arpa",
                        "DATABASE_URL": "postgres://db/app",
                        "PAPERLESS_URL": "https://paperless.docker.home.arpa",
                        "ALLOWED_HOSTS": "browser-use.docker.home.arpa,localhost",
                    },
                    "labels": {
                        "traefik.http.routers.app.rule": "Host(`app.docker.home.arpa`)"
                    },
                    "volumes": [
                        {"type": "volume", "source": "data", "target": "/data"}
                    ],
                }
                for name in names
            },
            "volumes": {"data": {}},
        }
    imagefile = tmp_path / "images.json"
    imagefile.write_text(json.dumps(images))
    monkeypatch.setattr(module, "IMAGES", imagefile)
    calls = []

    def compose(stack, *, override=False):
        """Record selection of the source's runtime Compose files."""
        calls.append((stack, override))
        return copy.deepcopy(configs[stack])

    monkeypatch.setattr(module.access, "compose", compose)
    return module, tmp_path, calls


def test_staging_keeps_data_settings_and_defers_handover(stateful, capsys):
    """Keep SMTP and secrets intact while adapting only named UI fields."""
    module, root, calls = stateful
    module.prepare()
    first = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    module.prepare()
    assert first == {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert calls[:4] == [
        ("paperless-ai", False),
        ("browser-use", False),
        ("crabberbot", True),
        ("send-bills", False),
    ]
    for stack in ("paperless-ai", "browser-use", "crabberbot", "send-bills"):
        path = root / stack / "atlas-compose.json"
        config = json.loads(path.read_text())
        assert path.stat().st_mode & 0o777 == 0o600
        assert config["volumes"] == {"data": {}}
        for service in config["services"].values():
            assert service["image"] == "sha256:" + "a" * 64
            assert service["build"]["context"] == str(root / stack)
            assert service["cap_drop"] == ["ALL"] and service["read_only"]
            assert service["volumes"][0]["source"] == "data"
            env = service["environment"]
            assert env["SECRET"] == "sensitive-docker.home.arpa"
            assert env["DJANGO_EMAIL_HOST"] == "docker.home.arpa"
            assert env["DATABASE_URL"] == "postgres://db/app"
            assert env["PAPERLESS_URL"] == "https://paperless.atlas.home.arpa"
            assert env["ALLOWED_HOSTS"] == "browser-use.atlas.home.arpa,localhost"
            assert (
                "app.atlas.home.arpa"
                in service["labels"]["traefik.http.routers.app.rule"]
            )
        if stack == "crabberbot":
            assert "cloudflared" not in config["services"]
        if stack == "send-bills":
            assert config["services"]["scheduler"]["profiles"] == ["migration-handover"]
        if stack == "paperless-ai":
            assert (
                config["services"]["webserver"]["environment"][
                    "PAPERLESS_EMAIL_TASK_CRON"
                ]
                == "disable"
            )
    assert capsys.readouterr().out == ""


def test_invalid_source_image_id_is_rejected(stateful):
    """Do not silently substitute a moving tag for the exported runtime image."""
    module, root, _ = stateful
    images = json.loads(module.IMAGES.read_text())
    images["paperless-ai"]["webserver"] = "source:latest"
    module.IMAGES.write_text(json.dumps(images))
    with pytest.raises(RuntimeError, match="source image ID"):
        module.prepare()
    assert not (root / "paperless-ai/atlas-compose.json").exists()
