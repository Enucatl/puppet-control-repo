#!/usr/bin/env python3
"""Prepare the copied Atlas access stacks without starting containers."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path("/opt/docker")
SUBUID = Path("/etc/subuid")
DOCKER_SOCKET = Path("/var/run/docker.sock")
# Approved local segments from provisioning/host_vars/router.yml.
LOCAL_ULA_NETWORKS = (
    "fd6b:d722:1738:0::/64",
    "fd6b:d722:1738:a::/64",
    "fd6b:d722:1738:28::/64",
    "fd6b:d722:1738:c8::/64",
)
AUTHELIA_IMAGES = {
    "authelia": "authelia/authelia@sha256:bd97cff4fcbf715b5ff1f9ae286afbe6033afce385302520b0368122d43a6f54",
    "postgres": "postgres@sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336",
    "redis": "redis@sha256:2c2dff791878316e3b083188be0d578423b24813cdda5de0e80ea4f7b3e6bfd6",
}


def write_private(path: Path, text: str) -> None:
    """Atomically replace configuration while retaining its first private backup."""
    if path.exists() and path.read_text() == text:
        os.chmod(path, 0o600)
        return
    if path.exists():
        backup = path.with_name(path.name + ".pre-atlas")
        if not backup.exists():
            with backup.open("xb") as stream:
                os.chmod(backup, 0o600)
                stream.write(path.read_bytes())
        os.chmod(backup, 0o600)
    owner = path.stat() if path.exists() else path.parent.stat()
    descriptor, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        os.fchown(descriptor, owner.st_uid, owner.st_gid)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def compose(stack: str) -> dict:
    """Resolve source Compose privately, including both environment files."""
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(ROOT / ".env"),
            "--env-file",
            str(ROOT / stack / ".env"),
            "-f",
            str(ROOT / stack / "docker-compose.yml"),
            "config",
            "--format=json",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError(
            f"Cannot resolve {stack} Compose; diagnostic output withheld"
        )
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise RuntimeError(f"Invalid {stack} Compose JSON; output withheld") from None


def prepare() -> None:
    """Render Atlas overrides and endpoints, keeping resolved source settings."""
    import yaml

    mappings = [
        line.split(":")
        for line in SUBUID.read_text().splitlines()
        if line.startswith("dockremap:")
    ]
    if len(mappings) != 1 or mappings[0][1] != "100000" or int(mappings[0][2]) < 65536:
        raise RuntimeError("Expected dockremap subordinate UID range 100000:65536")

    environment = (ROOT / ".env").read_text()
    environment, count = re.subn(
        r"(?m)^(?:export\s+)?DOCKER_DOMAIN=.*$",
        "DOCKER_DOMAIN=atlas.home.arpa",
        environment,
    )
    if not count:
        environment = environment.rstrip("\n") + "\nDOCKER_DOMAIN=atlas.home.arpa\n"
    write_private(ROOT / ".env", environment)

    runtime_configs = []
    for stack in ("traefik", "authelia"):
        config = compose(stack)
        services = config["services"]
        if stack == "traefik":
            services["socket-proxy"]["user"] = f"0:{DOCKER_SOCKET.stat().st_gid}"
            service = services["traefik"]
            service["image"] = "ghcr.io/enucatl/traefik:latest"
            healthcheck = service.get("healthcheck", {}).get("test", [])
            if healthcheck[:2] == ["CMD", "traefik"]:
                healthcheck[1] = "/traefik"
            volumes = service["volumes"]
            old_targets = {
                "/run/mtls-ca/docker-root-ca.crt",
                "/run/mtls-ca/docker-intermediate-ca.crt",
            }
            volumes[:] = [
                volume for volume in volumes if volume.get("target") not in old_targets
            ]
            volumes.append(
                {
                    "type": "bind",
                    "source": "/usr/local/share/ca-certificates/atlas-infrastructure.crt",
                    "target": "/run/mtls-ca/atlas-root-ca.crt",
                    "read_only": True,
                }
            )
            for path in sorted((ROOT / stack / "data").rglob("*")):
                if path.suffix not in (".yml", ".yaml") or not path.is_file():
                    continue
                text = path.read_text().replace("docker.home.arpa", "atlas.home.arpa")
                if path == ROOT / stack / "data/config.yml":
                    dynamic = yaml.safe_load(text)
                    ranges = dynamic["http"]["middlewares"]["default-allowlist"][
                        "ipAllowList"
                    ]["sourceRange"]
                    ranges.extend(
                        network
                        for network in LOCAL_ULA_NETWORKS
                        if network not in ranges
                    )
                    for store in dynamic.get("tls", {}).get("stores", {}).values():
                        certificate = store.get("defaultCertificate", {})
                        for key in ("certFile", "keyFile"):
                            if key in certificate:
                                certificate[key] = (
                                    certificate[key]
                                    .replace("docker_fullchain", "atlas_fullchain")
                                    .replace("docker_key", "atlas_key")
                                )
                    for option in dynamic.get("tls", {}).get("options", {}).values():
                        if "clientAuth" in option:
                            option["clientAuth"]["caFiles"] = [
                                "/run/mtls-ca/atlas-root-ca.crt"
                            ]
                    text = yaml.safe_dump(dynamic, sort_keys=False)
                write_private(path, text)
                runtime_configs.append(path)
        else:
            for service in services.values():
                image = service.get("image", "").split("@")[0].split(":")[0]
                name = image.rsplit("/", 1)[-1]
                if name in AUTHELIA_IMAGES:
                    service["image"] = AUTHELIA_IMAGES[name]
            services["authelia"].setdefault("environment", {})[
                "AUTHELIA_AUTHENTICATION_BACKEND_LDAP_ADDRESS"
            ] = "ldaps://ipa.home.arpa"
            path = ROOT / stack / "config/configuration.yml"
            text = (
                path.read_text()
                .replace("docker.home.arpa", "atlas.home.arpa")
                .replace("freeipa.home.arpa", "ipa.home.arpa")
            )
            # Parse only access rules; other sections contain unquoted Go templates.
            section = re.search(r"(?ms)^access_control:\n.*?(?=^\S|\Z)", text)
            if section is None:
                raise RuntimeError("Authelia access control section is missing")
            access = yaml.safe_load(section.group())
            local = next(
                network
                for network in access["access_control"]["networks"]
                if network["name"] == "local"
            )["networks"]
            local.extend(
                network for network in LOCAL_ULA_NETWORKS if network not in local
            )
            text = (
                text[: section.start()]
                + yaml.safe_dump(access, sort_keys=False)
                + text[section.end() :]
            )
            write_private(path, text)
            runtime_configs.append(path)
        write_private(
            ROOT / stack / "atlas-compose.json", json.dumps(config, indent=2) + "\n"
        )

    # UID 1000 inside dockremap containers is host UID 101000.
    certs = ROOT / "traefik/data/certs"
    stack_owner = (ROOT / "traefik").stat()
    os.chown(certs, stack_owner.st_uid, stack_owner.st_gid)
    for permissions, paths in (
        ("u:101000:r-x", [certs]),
        (
            "u:101000:r--",
            runtime_configs + [certs / "atlas_fullchain.pem", certs / "atlas_key.pem"],
        ),
    ):
        result = subprocess.run(
            ["setfacl", "-m", permissions, "--", *(str(path) for path in paths)],
            capture_output=True,
            text=True,
        )
        if result.returncode:
            raise RuntimeError(
                "Cannot grant access-stack runtime ACLs; diagnostics withheld"
            )


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
    print("Prepared Traefik and Authelia; containers were not started")
