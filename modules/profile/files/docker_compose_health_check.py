#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterable
from typing import Any


JsonValue = dict[str, Any] | list[Any] | str | int | float | bool | None


def flatten_containers(value: JsonValue) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        return

    if isinstance(value, list):
        for item in value:
            yield from flatten_containers(item)


def parse_ps_output(output: str) -> list[dict[str, Any]]:
    stripped = output.strip()
    if not stripped:
        return []

    try:
        return list(flatten_containers(json.loads(stripped)))
    except json.JSONDecodeError:
        containers: list[dict[str, Any]] = []
        for line in stripped.splitlines():
            containers.extend(flatten_containers(json.loads(line)))
        return containers


def exit_code_is_zero(value: object) -> bool:
    if value is None:
        return True
    try:
        return int(str(value)) == 0
    except ValueError:
        return False


def is_acceptable(container: dict[str, Any]) -> bool:
    state = container.get("State")
    health = container.get("Health")

    if state == "running":
        return health != "unhealthy"

    if state == "exited":
        return exit_code_is_zero(container.get("ExitCode"))

    return False


def container_name(container: dict[str, Any]) -> str:
    for key in ("Name", "Service", "ID"):
        value = container.get(key)
        if value:
            return str(value)
    return "<unknown>"


def main(argv: list[str]) -> int:
    if not argv:
        print(
            "usage: docker-compose-health-check <docker compose command...>",
            file=sys.stderr,
        )
        return 2

    command = argv + ["ps", "--all", "--format", "json"]
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as error:
        if error.stdout:
            print(error.stdout, end="", file=sys.stderr)
        if error.stderr:
            print(error.stderr, end="", file=sys.stderr)
        return error.returncode

    try:
        containers = parse_ps_output(result.stdout)
    except json.JSONDecodeError as error:
        print(f"failed to parse docker compose ps JSON: {error}", file=sys.stderr)
        return 3

    failed = [container for container in containers if not is_acceptable(container)]
    for container in failed:
        print(
            "container health check failed: "
            f"{container_name(container)} "
            f"state={container.get('State')} "
            f"health={container.get('Health')} "
            f"exit_code={container.get('ExitCode')}",
            file=sys.stderr,
        )

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
