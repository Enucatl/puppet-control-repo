"""Verify background staging preserves data and defers shared identities."""

import copy
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


@pytest.fixture
def background(tmp_path, monkeypatch):
    """Load private source-like manifests with no live Docker operations."""
    spec = importlib.util.spec_from_file_location(
        "atlas_background_prepare",
        Path(__file__).parents[1] / "proxmox/atlas-background-prepare.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.access, "ROOT", tmp_path)
    (tmp_path / ".env").write_text("DOCKER_DOMAIN=atlas.home.arpa\n")
    names = {
        "kms": ["server", "gui"],
        "teamspeak": ["teamspeak"],
        "airflow": ["postgres-outputs", "exam"],
        "protonmail-bridge": ["protonmail-bridge"],
        "rss-to-telegram-bot": ["main"],
        "tor-i2pd": ["i2pd", "cloudflare-ddns"],
        "rclone": ["rclone", "cloudflared"],
        "backrest": ["backrest", "backrest-init"],
        "qbittorrent": ["qbittorrent", "config"],
        "barbero-scripts": ["caddy", "cloudflared"],
    }
    configs = {}
    manifest = {}
    for stack, services in names.items():
        (tmp_path / stack).mkdir()
        configs[stack] = {
            "services": {
                name: {
                    "image": "source:latest",
                    "read_only": True,
                    "cap_drop": ["ALL"],
                    "environment": {
                        "SECRET": "secret-docker.home.arpa",
                        "MAIL_HOST": "docker.home.arpa",
                    },
                    "labels": {
                        "traefik.http.routers.app.rule": "Host(`app.docker.home.arpa`)",
                        "other": "docker.home.arpa",
                    },
                    "volumes": [
                        {"type": "volume", "source": "data", "target": "/data"}
                    ],
                }
                for name in services
            },
            "volumes": {"data": {}},
        }
        manifest[stack] = {
            "images": {services[0]: "sha256:" + "a" * 64},
            "volumes": [],
        }
    configs["airflow"]["services"]["exam"]["profiles"] = ["runner"]
    configs["rclone"]["services"]["rclone"]["depends_on"] = {
        "cloudflared": {"condition": "service_healthy"}
    }
    configs["protonmail-bridge"]["services"]["protonmail-bridge"]["volumes"].append(
        {
            "type": "bind",
            "source": str(tmp_path / "traefik/data/certs/docker_key.pem"),
            "target": "/protonmail/certs/key.pem",
            "read_only": True,
        }
    )
    for name in names["backrest"]:
        configs["backrest"]["services"][name]["volumes"].append(
            {
                "type": "bind",
                "source": "/source-authority/key",
                "target": "/vault-unseal/key",
            }
        )
    key = tmp_path / "atlas-authority/key"
    key.parent.mkdir()
    key.write_text("never-read-this")
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 0, str(key.parent) + "\n", ""
        ),
    )
    manifestfile = tmp_path / "source-state.json"
    manifestfile.write_text(json.dumps(manifest))
    monkeypatch.setattr(module, "MANIFEST", manifestfile)
    monkeypatch.setattr(
        module.access, "compose", lambda stack: copy.deepcopy(configs[stack])
    )
    return module, tmp_path, names


def test_prepare_defers_shared_identities_and_preserves_source(background, capsys):
    """Leave only independent endpoints eligible and retain all source secrets."""
    module, root, names = background
    module.prepare()
    first = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    module.prepare()
    assert first == {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert capsys.readouterr().out == ""
    eligible = set()
    for stack, services in names.items():
        path = root / stack / "atlas-compose.json"
        config = json.loads(path.read_text())
        assert path.stat().st_mode & 0o777 == 0o600
        assert config["volumes"] == {"data": {}}
        for name, service in config["services"].items():
            if not service.get("profiles"):
                eligible.add((stack, name))
            assert service["image"] == (
                "sha256:" + "a" * 64 if name == services[0] else "source:latest"
            )
            assert service["read_only"] and service["cap_drop"] == ["ALL"]
            assert service["environment"] == {
                "SECRET": "secret-docker.home.arpa",
                "MAIL_HOST": "docker.home.arpa",
            }
            assert service["labels"]["other"] == "docker.home.arpa"
            assert (
                "app.atlas.home.arpa"
                in service["labels"]["traefik.http.routers.app.rule"]
            )
            if stack in module.DEFERRED or name == "cloudflared":
                assert service["profiles"] == ["migration-handover"]
        if stack == "rclone":
            assert not config["services"]["rclone"]["depends_on"]
        if stack == "airflow":
            assert config["services"]["exam"]["profiles"] == ["runner"]
        if stack == "backrest":
            for service in config["services"].values():
                assert service["volumes"][-1]["source"] == str(
                    root / "atlas-authority/key"
                )
                assert service["volumes"][-1]["bind"]["create_host_path"] is False
        if stack == "protonmail-bridge":
            assert config["services"]["protonmail-bridge"]["volumes"][-1] == {
                "type": "bind",
                "source": str(root / "traefik/data/certs/atlas_key.pem"),
                "target": "/protonmail/certs/key.pem",
                "read_only": True,
            }
    assert eligible == {
        ("kms", "server"),
        ("kms", "gui"),
        ("teamspeak", "teamspeak"),
        ("airflow", "postgres-outputs"),
        ("rclone", "rclone"),
    }


def test_missing_atlas_authority_fails_before_rendering(background):
    """Reject missing Atlas authority rather than copying or creating its key."""
    module, root, _ = background
    (root / "atlas-authority/key").unlink()
    with pytest.raises(RuntimeError, match="authority key is missing"):
        module.prepare()
    assert not list(root.rglob("atlas-compose.json"))


def test_invalid_source_image_is_rejected(background):
    """Reject an unverified image reference instead of pulling a different build."""
    module, root, _ = background
    manifest = json.loads(module.MANIFEST.read_text())
    manifest["kms"]["images"]["server"] = "source:latest"
    module.MANIFEST.write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError, match="Expected source image ID"):
        module.prepare()
    assert not list(root.rglob("atlas-compose.json"))
