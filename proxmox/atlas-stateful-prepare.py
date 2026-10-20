#!/usr/bin/env python3
"""Render copied stateful stacks for Atlas while deferring public job handover."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re

SPEC = importlib.util.spec_from_file_location(
    "atlas_access_prepare", Path(__file__).with_name("atlas-access-prepare.py")
)
assert SPEC and SPEC.loader
access = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(access)
IMAGES = Path(__file__).with_name("atlas-stateful-images.json")
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


def prepare() -> None:
    """Preserve source settings and secrets, adapting named routes and UI endpoints."""
    root = access.ROOT
    if not re.search(
        r"(?m)^DOCKER_DOMAIN=atlas\.home\.arpa$", (root / ".env").read_text()
    ):
        raise RuntimeError("Prepare the Atlas access environment first")
    images = json.loads(IMAGES.read_text())
    for stack in ("paperless-ai", "browser-use", "crabberbot", "send-bills"):
        config = access.compose(stack, override=stack == "crabberbot")
        services = config["services"]
        for name, service in services.items():
            image = images[stack][name]
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
                raise RuntimeError(
                    f"Expected exported source image ID for {stack}/{name}"
                )
            service["image"] = image
            for key, value in service.get("labels", {}).items():
                if key.startswith("traefik."):
                    service["labels"][key] = value.replace(
                        "docker.home.arpa", "atlas.home.arpa"
                    )
            environment = service.setdefault("environment", {})
            for key in ENDPOINT_FIELDS & environment.keys():
                environment[key] = environment[key].replace(
                    "docker.home.arpa", "atlas.home.arpa"
                )
        if stack == "send-bills":
            services["scheduler"]["profiles"] = ["migration-handover"]
        if stack == "paperless-ai":
            # Source mail accounts remain active until the explicit handover.
            services["webserver"]["environment"]["PAPERLESS_EMAIL_TASK_CRON"] = (
                "disable"
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
    print("Prepared stateful stack manifests; containers were not started")
