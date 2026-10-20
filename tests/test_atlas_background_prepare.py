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
        "airflow": [
            "postgres-outputs",
            "exam",
            "download-zanzara",
            "cyber-analyst",
            "operations-analyst",
            "podcast-statistics",
            "puppet-release-watch",
        ],
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
    for name in names["airflow"][1:]:
        runner = configs["airflow"]["services"][name]
        runner["profiles"] = ["runner"]
        runner["environment"].update(
            {key: "https://source.docker.home.arpa" for key in module.AIRFLOW_ENDPOINTS}
        )
        runner["volumes"].append(
            {"type": "bind", "source": "/scratch/archive", "target": "/scratch/archive"}
        )
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

    def compose(stack, *, profiles=()):
        """Require runner selection to retain all source Airflow services."""
        assert profiles == (("runner",) if stack == "airflow" else ())
        return copy.deepcopy(configs[stack])

    monkeypatch.setattr(module.access, "compose", compose)
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
            expected_environment = {
                "SECRET": "secret-docker.home.arpa",
                "MAIL_HOST": "docker.home.arpa",
            }
            if stack == "airflow" and name != "postgres-outputs":
                expected_environment.update(module.AIRFLOW_ENDPOINTS)
            if stack == "tor-i2pd" and name == "cloudflare-ddns":
                expected_environment["DOMAINS"] = (
                    "teamspeak.enucatl.com,torrent.enucatl.com"
                )
            assert service["environment"] == expected_environment
            assert service["labels"]["other"] == "docker.home.arpa"
            assert (
                "app.atlas.home.arpa"
                in service["labels"]["traefik.http.routers.app.rule"]
            )
            if (
                stack in module.DEFERRED
                or name == "cloudflared"
                or (stack == "tor-i2pd" and name == "cloudflare-ddns")
            ):
                assert service["profiles"] == ["migration-handover"]
        if stack == "rclone":
            assert not config["services"]["rclone"]["depends_on"]
        if stack == "airflow":
            for name in services[1:]:
                assert config["services"][name]["profiles"] == ["runner"]
                volumes = config["services"][name]["volumes"]
                scratch = [v for v in volumes if v["target"] == "/scratch/archive"]
                assert bool(scratch) == (name == "download-zanzara")
                assert volumes[0] == {
                    "type": "volume",
                    "source": "data",
                    "target": "/data",
                }
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
        ("rss-to-telegram-bot", "main"),
        ("protonmail-bridge", "protonmail-bridge"),
        ("tor-i2pd", "i2pd"),
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


def test_atlas_jobs_preserve_schedules_and_single_active_owner():
    """Preserve timer schedules while giving migrated jobs one active owner."""
    import yaml

    nodes = Path(__file__).parents[1] / "data/nodes"
    atlas = yaml.safe_load((nodes / "atlas.home.arpa.yaml").read_text())[
        "profile::common::systemd::units"
    ]
    source = yaml.safe_load((nodes / "docker.home.arpa.yaml").read_text())[
        "profile::common::systemd::units"
    ]
    schedules = {
        "exam": "*-*-* 00,05..23:00:00 UTC",
        "cyber-analyst": "*-*-01 03:00:00 UTC",
        "operations-analyst": "Fri *-*-* 02:00:00 UTC",
        "podcast-statistics": "*-*-* *:00/15:00 UTC",
        "download-zanzara": "*-*-* 00:00:00 UTC",
    }
    for name, schedule in schedules.items():
        for suffix in ("service", "timer"):
            unit = f"automation-{name}.{suffix}"
            enabled = suffix == "timer" and name != "operations-analyst"
            assert atlas[unit]["enable"] is enabled and atlas[unit]["active"] is enabled
            assert source[unit]["ensure"] == "absent"
            assert source[unit]["enable"] is False and source[unit]["active"] is False
        service = atlas[f"automation-{name}.service"]["content"]
        assert "User=user\n" in service
        assert (
            "ExecStart=/usr/bin/docker compose --env-file /dev/null -f atlas-compose.json --profile runner "
            f"run --pull never --rm --no-deps {name}\n"
        ) in service
        assert (
            f"OnCalendar={schedule}\n" in atlas[f"automation-{name}.timer"]["content"]
        )
    assert (
        "RequiresMountsFor=/scratch"
        in atlas["automation-download-zanzara.service"]["content"]
    )
