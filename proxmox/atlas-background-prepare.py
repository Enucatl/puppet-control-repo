#!/usr/bin/env python3
"""Render copied background stacks without activating shared identities or jobs."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess

SPEC = importlib.util.spec_from_file_location(
    "atlas_access_prepare", Path(__file__).with_name("atlas-access-prepare.py")
)
assert SPEC and SPEC.loader
access = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(access)
MANIFEST = Path("/root/openvox-infra/stacks/source-state.json")
STACKS = (
    "kms",
    "teamspeak",
    "airflow",
    "protonmail-bridge",
    "rss-to-telegram-bot",
    "tor-i2pd",
    "rclone",
    "backrest",
    "qbittorrent",
    "barbero-scripts",
)
DEFERRED = {
    "protonmail-bridge",
    "backrest",
    "qbittorrent",
    "barbero-scripts",
}
AIRFLOW_ENDPOINTS = {
    "VAULT_ADDR": "https://vault.home.arpa:8200",
    "LOKI_READ_URL": "https://loki-read.atlas.home.arpa",
    "PHOENIX_COLLECTOR_ENDPOINT": "https://phoenix-otlp.atlas.home.arpa/v1/traces",
}


def atlas_unseal_key() -> Path:
    """Resolve the existing Atlas authority volume without reading its key."""
    result = subprocess.run(
        [
            "docker",
            "volume",
            "inspect",
            "infra_vault_unseal",
            "--format={{.Mountpoint}}",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError("Cannot resolve the existing Atlas Vault authority volume")
    key = Path(result.stdout.strip()) / "key"
    if not key.is_absolute() or not key.is_file():
        raise RuntimeError("Existing Atlas Vault authority key is missing")
    return key


def prepare() -> None:
    """Preserve source settings, pin running images, and defer shared workloads."""
    root = access.ROOT
    if not re.search(
        r"(?m)^DOCKER_DOMAIN=atlas\.home\.arpa$", (root / ".env").read_text()
    ):
        raise RuntimeError("Prepare the Atlas access environment first")
    manifest = json.loads(MANIFEST.read_text())
    unseal = atlas_unseal_key()
    for stack in STACKS:
        config = (
            access.compose(stack, profiles=("runner",))
            if stack == "airflow"
            else access.compose(stack)
        )
        for name, service in config["services"].items():
            image = manifest[stack]["images"].get(name)
            if image is not None:
                if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
                    raise RuntimeError(f"Expected source image ID for {stack}/{name}")
                service["image"] = image
            for key, value in service.get("labels", {}).items():
                if key.startswith("traefik."):
                    service["labels"][key] = value.replace(
                        "docker.home.arpa", "atlas.home.arpa"
                    )
            if (
                stack in DEFERRED
                or name == "cloudflared"
                or (stack == "tor-i2pd" and name == "cloudflare-ddns")
            ):
                service["profiles"] = ["migration-handover"]
            if stack == "rclone" and name == "rclone":
                # The source tunnel stays authoritative until its explicit handover.
                service.get("depends_on", {}).pop("cloudflared", None)
            if stack == "airflow" and "runner" in service.get("profiles", []):
                environment = service.setdefault("environment", {})
                for key in AIRFLOW_ENDPOINTS.keys() & environment.keys():
                    environment[key] = AIRFLOW_ENDPOINTS[key]
                if name != "download-zanzara":
                    # Only the downloader reads or writes the archive filesystem.
                    service["volumes"] = [
                        volume
                        for volume in service.get("volumes", [])
                        if volume.get("target") != "/scratch/archive"
                    ]
            for volume in service.get("volumes", []):
                if stack == "protonmail-bridge" and volume.get("type") == "bind":
                    for old, new in (
                        ("docker_fullchain.pem", "atlas_fullchain.pem"),
                        ("docker_key.pem", "atlas_key.pem"),
                    ):
                        source = str(root / "traefik/data/certs" / old)
                        if volume["source"] == source:
                            volume["source"] = str(root / "traefik/data/certs" / new)
                if stack == "backrest" and volume.get("target") in {
                    "/vault-unseal/key",
                    "/source/var/lib/docker/100000.100000/volumes/infra_vault_unseal/_data/key",
                }:
                    volume["source"] = str(unseal)
                    volume.setdefault("bind", {})["create_host_path"] = False
        access.write_compose(root / stack / "atlas-compose.json", config)


if __name__ == "__main__":
    if os.geteuid() != 0:
        raise SystemExit("Run as root on Atlas")
    os.umask(0o077)
    try:
        prepare()
    except Exception as error:
        raise SystemExit(
            f"Preparation failed ({type(error).__name__}); configuration withheld"
        ) from None
    print("Prepared background stack manifests; containers were not started")
