#!/usr/bin/python3
"""Move Atlas adaptations into ordinary Compose files without starting stacks."""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile

import yaml

ROOT = Path("/opt/docker")
STACKS = (
    "airflow",
    "ais",
    "authelia",
    "backrest",
    "barbero-scripts",
    "beszel",
    "browser-use",
    "crabberbot",
    "grafana-loki",
    "kms",
    "paperless-ai",
    "protonmail-bridge",
    "qbittorrent",
    "rclone",
    "rss-to-telegram-bot",
    "send-bills",
    "teamspeak",
    "tor-i2pd",
    "traefik",
)
ENDPOINT_FIELDS = {
    "PAPERLESS_URL",
    "PAPERLESS_LOGOUT_REDIRECT_URL",
    "PAPERLESS_ALLOWED_HOSTS",
    "PAPERLESS_CSRF_TRUSTED_ORIGINS",
    "PAPERLESS_CORS_ALLOWED_HOSTS",
    "BROWSER_USE_HOST",
    "ALLOWED_HOSTS",
    "CSRF_TRUSTED_ORIGINS",
    "DJANGO_ALLOWED_HOSTS",
}
AIRFLOW_ENDPOINTS = {
    "VAULT_ADDR": "https://vault.home.arpa:8200",
    "LOKI_READ_URL": "https://loki-read.atlas.home.arpa",
    "PHOENIX_COLLECTOR_ENDPOINT": "https://phoenix-otlp.atlas.home.arpa/v1/traces",
}


def adapt(stack: str, config: dict) -> dict:
    """Apply maintained host differences without copying frozen images or secrets."""
    services = config["services"]
    for name, service in services.items():
        for key, value in service.get("labels", {}).items():
            if key.startswith("traefik."):
                service["labels"][key] = value.replace(
                    "docker.home.arpa", "atlas.home.arpa"
                )
        environment = service.get("environment", {})
        for key in ENDPOINT_FIELDS & environment.keys():
            if isinstance(environment[key], str):
                environment[key] = environment[key].replace(
                    "docker.home.arpa", "atlas.home.arpa"
                )
        if stack == "authelia" and name == "authelia":
            environment["AUTHELIA_AUTHENTICATION_BACKEND_LDAP_ADDRESS"] = (
                "ldaps://ipa.home.arpa"
            )
        if stack == "airflow" and "runner" in service.get("profiles", []):
            for key in AIRFLOW_ENDPOINTS.keys() & environment.keys():
                environment[key] = AIRFLOW_ENDPOINTS[key]
            if name != "download-zanzara":
                service["volumes"] = [
                    volume
                    for volume in service.get("volumes", [])
                    if volume.get("target") != "/scratch/archive"
                ]
        if stack == "tor-i2pd" and name == "cloudflare-ddns":
            environment["DOMAINS"] = "teamspeak.enucatl.com,torrent.enucatl.com"
        if environment:
            service["environment"] = environment
        if stack == "protonmail-bridge":
            for volume in service.get("volumes", []):
                if volume.get("type") == "bind":
                    for old, new in (
                        ("docker_fullchain.pem", "atlas_fullchain.pem"),
                        ("docker_key.pem", "atlas_key.pem"),
                    ):
                        if volume["source"] == str(ROOT / "traefik/data/certs" / old):
                            volume["source"] = str(ROOT / "traefik/data/certs" / new)
    if stack == "traefik":
        services["socket-proxy"]["user"] = (
            f"0:{Path('/var/run/docker.sock').stat().st_gid}"
        )
        service = services["traefik"]
        service["image"] = "ghcr.io/enucatl/traefik:latest"
        healthcheck = service.get("healthcheck", {}).get("test", [])
        if healthcheck[:2] == ["CMD", "traefik"]:
            healthcheck[1] = "/traefik"
        service["volumes"] = [
            volume
            for volume in service.get("volumes", [])
            if volume.get("target")
            not in {
                "/run/mtls-ca/docker-root-ca.crt",
                "/run/mtls-ca/docker-intermediate-ca.crt",
                "/run/mtls-ca/atlas-root-ca.crt",
            }
        ] + [
            {
                "type": "bind",
                "source": "/usr/local/share/ca-certificates/atlas-infrastructure.crt",
                "target": "/run/mtls-ca/atlas-root-ca.crt",
                "read_only": True,
            }
        ]
    if stack == "grafana-loki":
        # The migrated Grafana DB and ML-DSA OIDC require this Go 1.27 build.
        services["grafana"]["image"] = (
            "grafana/grafana-image-tags:13.3.0-39395647570-amd64"
        )
        services["loki"]["ports"] = [
            {
                "target": 3100,
                "published": "3100",
                "host_ip": "127.0.0.1",
                "protocol": "tcp",
            }
        ]
    return config


class Override(list):
    """Replace a Compose sequence instead of extending its base values."""


yaml.SafeDumper.add_representer(
    Override, lambda dumper, value: dumper.represent_sequence("!override", value)
)
yaml.SafeLoader.add_constructor(
    "!override",
    lambda loader, node: Override(loader.construct_sequence(node, deep=True)),
)


def write_private(path: Path, text: str) -> None:
    """Keep one private rollback copy and atomically replace changed configuration."""
    if path.exists() and path.read_text() == text:
        return
    if path.exists():
        backup = path.with_name(path.name + ".pre-normalize")
        if not backup.exists():
            with backup.open("x") as output:
                os.chmod(backup, 0o600)
                output.write(path.read_text())
        exclude = path.parent / ".git/info/exclude"
        if exclude.parent.is_dir():
            rules = exclude.read_text() if exclude.exists() else ""
            if "*.pre-normalize" not in rules.splitlines():
                exclude.write_text(rules.rstrip("\n") + "\n*.pre-normalize\n")
    descriptor, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        owner = path.parent.stat()
        os.fchown(descriptor, owner.st_uid, owner.st_gid)
        with os.fdopen(descriptor, "w") as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def normalize_environment(directory: Path) -> None:
    """Make ordinary Compose self-contained while preserving project secrets."""
    import re

    path = directory / ".env"
    text = path.read_text() if path.exists() else ""
    shared = {}
    for line in (ROOT / ".env").read_text().splitlines():
        match = re.match(r"(?:export\s+)?([A-Za-z_][A-Za-z_0-9]*)=", line)
        if match:
            shared[match[1]] = line
    project = []
    # Define shared values before project references; retain project overrides.
    for line in text.splitlines(keepends=True):
        match = re.match(r"(?:export\s+)?([A-Za-z_][A-Za-z_0-9]*)=", line)
        if match and match[1] in shared:
            shared[match[1]] = line.rstrip("\n")
        else:
            project.append(line)
    text = "\n".join(shared.values()) + "\n" + "".join(project)
    text = re.sub(
        r"(?m)^((?:export\s+)?DOCKER_DOMAIN)=.*$", r"\1=atlas.home.arpa", text
    )
    for key in ENDPOINT_FIELDS:
        text = re.sub(
            rf"(?m)^((?:export\s+)?{key}=)(.*)$",
            lambda match: (
                match[1] + match[2].replace("docker.home.arpa", "atlas.home.arpa")
            ),
            text,
        )
    write_private(path, text)


def normalize(stack: str) -> None:
    """Preserve source files and add only named Atlas differences to their override."""
    directory = ROOT / stack
    if stack == "beszel":
        path = directory / "config.yml"
        inventory = yaml.safe_load(path.read_text())
        migrations = {
            "docker.home.arpa": "atlas.home.arpa",
            "complex.home.arpa": "cognition.home.arpa",
            "forbearance.home.arpa": "aperture.home.arpa",
        }
        names = {system["name"] for system in inventory["systems"]}
        # Beszel deletes records absent from this inventory, including history.
        for system in list(inventory["systems"]):
            name = system["name"]
            if name in migrations and migrations[name] not in names:
                canonical = {
                    **system,
                    "name": migrations[name],
                    "host": migrations[name],
                }
                canonical.pop("id", None)
                inventory["systems"].append(canonical)
                names.add(migrations[name])
            elif name in migrations.values():
                system["host"] = name
        write_private(path, yaml.safe_dump(inventory, sort_keys=False))
        path = directory / "alerts.yml"
        alerts = yaml.safe_load(path.read_text())
        alerts["systems"] = {
            migrations.get(name, name): rules
            for name, rules in alerts["systems"].items()
        }
        write_private(path, yaml.safe_dump(alerts, sort_keys=False))
    normalize_environment(directory)
    if stack == "backrest":
        template = directory / "backrest/config.template.json"
        config = json.loads(template.read_text())
        documents = next(plan for plan in config["plans"] if plan["id"] == "documents")
        documents["schedule"] = {"cron": "0 0 * * 1", "clock": "CLOCK_LOCAL"}
        write_private(template, json.dumps(config, indent=2) + "\n")
        template.chmod(0o644)
    environment = os.environ.copy()
    environment.pop("COMPOSE_ENV_FILES", None)
    environment.pop("COMPOSE_DISABLE_ENV_FILE", None)
    command = ["/usr/bin/docker", "compose", "--env-file", str(ROOT / ".env")]
    if (directory / ".env").exists():
        command += ["--env-file", str(directory / ".env")]
    command += ["-f", str(directory / "docker-compose.yml")]
    override_path = directory / "docker-compose.override.yml"
    override = (
        yaml.safe_load(override_path.read_text()) if override_path.exists() else {}
    )
    if override_path.exists():
        command += ["-f", str(override_path)]
    if stack == "airflow":
        command += ["--profile", "runner"]
    result = subprocess.run(
        command + ["config", "--format=json"],
        env=environment,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError(
            f"Cannot resolve {stack} source Compose; configuration withheld"
        )
    source = json.loads(result.stdout)
    adapted = adapt(stack, copy.deepcopy(source))
    changed = False
    for name, service in adapted["services"].items():
        prior = source["services"][name]
        updates = {}
        for key in ("image", "user", "healthcheck", "ports", "volumes"):
            if service.get(key) != prior.get(key):
                updates[key] = (
                    Override(service[key])
                    if key in {"ports", "volumes"}
                    else service[key]
                )
        for key in ("environment", "labels"):
            values = {
                field: value
                for field, value in service.get(key, {}).items()
                if value != prior.get(key, {}).get(field)
            }
            if values:
                updates[key] = values
        if updates:
            target = override.setdefault("services", {}).setdefault(name, {})
            for key, value in updates.items():
                if key in {"environment", "labels"}:
                    target.setdefault(key, {}).update(value)
                else:
                    target[key] = value
            changed = True
    if changed:
        write_private(override_path, yaml.safe_dump(override, sort_keys=False))


def main() -> None:
    """Normalize selected stack sources without touching containers or old renders."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stack", choices=STACKS, nargs="+")
    arguments = parser.parse_args()
    os.umask(0o077)
    for stack in arguments.stack:
        normalize(stack)
        print(f"Normalized ordinary Compose sources for {stack}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        raise SystemExit(
            f"Atlas Compose normalization failed ({type(error).__name__}); configuration withheld"
        ) from None
