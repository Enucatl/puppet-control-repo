#!/usr/bin/env python3
"""Prepare copied observability stacks without starting containers or old jobs."""

from __future__ import annotations

import importlib.util
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
IMAGES = {
    "grafana": "sha256:62ded2143ad839ed656476b68592849fc76144d4a52af90d752492a7f45fdc7d",
    "loki": "grafana/loki@sha256:1107dd5274e0ada47e42472b7a7e71f3b2a2fe878878108f3e2f9e51528f0193",
    "beszel": "henrygd/beszel@sha256:fefb27166f5e1611ebf67f8697ea928a23f44efdb00af922e2ac3b5faa2efd5c",
}


def grafana_endpoints(text: str) -> str:
    """Update only server and OAuth endpoints, preserving comments and secrets."""
    section = ""
    lines = []
    fields = {
        "server": {"domain", "root_url"},
        "auth.generic_oauth": {"auth_url", "token_url", "api_url"},
    }
    for line in text.splitlines(keepends=True):
        header = re.match(r"^\s*\[([^]]+)\]", line)
        if header:
            section = header[1]
        field = re.match(r"^\s*([\w]+)\s*=", line)
        if field and field[1] in fields.get(section, set()):
            line = line.replace("docker.home.arpa", "atlas.home.arpa")
        lines.append(line)
    return "".join(lines)


def prepare() -> None:
    """Render pinned stacks with Atlas routes and private readable bind configs."""
    root = access.ROOT
    if not re.search(
        r"(?m)^DOCKER_DOMAIN=atlas\.home\.arpa$", (root / ".env").read_text()
    ):
        raise RuntimeError("Prepare the Atlas access environment first")
    mappings = [
        line.split(":")
        for line in access.SUBUID.read_text().splitlines()
        if line.startswith("dockremap:")
    ]
    if len(mappings) != 1 or mappings[0][1] != "100000" or int(mappings[0][2]) < 65536:
        raise RuntimeError("Expected dockremap subordinate UID range 100000:65536")
    for stack in ("grafana-loki", "beszel"):
        config = access.compose(stack)
        services = config["services"]
        if stack == "beszel":
            # Staging must not run the source's alert provisioning job.
            services.pop("alerts-init", None)
        for name, service in services.items():
            service["image"] = IMAGES[name]
            for key, value in service.get("labels", {}).items():
                if key.startswith("traefik."):
                    service["labels"][key] = value.replace(
                        "docker.home.arpa", "atlas.home.arpa"
                    )
        if stack == "grafana-loki":
            # Native Alloy cannot negotiate the ML-DSA HTTPS ingress.
            services["loki"]["ports"] = [
                {
                    "target": 3100,
                    "published": "3100",
                    "host_ip": "127.0.0.1",
                    "protocol": "tcp",
                }
            ]
            path = root / stack / "grafana/grafana.ini"
            access.write_private(path, grafana_endpoints(path.read_text()))
            uid = 100472
        else:
            services["beszel"].setdefault("environment", {})["APP_URL"] = (
                "https://beszel.atlas.home.arpa"
            )
            path = root / stack / "config.yml"
            # Preserve YAML formatting and any Go templates rather than parsing secrets.
            text = re.sub(
                r"(?m)^(\s*host:\s*)[\'\"]?172\.17\.96\.1[\'\"]?(\s*(?:#.*)?)$",
                r"\1docker.home.arpa\2",
                path.read_text(),
            )
            access.write_private(path, text)
            uid = 100000
        result = subprocess.run(
            ["setfacl", "-m", f"u:{uid}:r--", "--", str(path)],
            capture_output=True,
            text=True,
        )
        if result.returncode:
            raise RuntimeError(
                "Cannot grant observability config ACL; diagnostics withheld"
            )
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
    print("Prepared Grafana, Loki and Beszel; containers were not started")
