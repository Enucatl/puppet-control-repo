"""Verify the shared and project environment contract without copying values."""

import json
import os
from pathlib import Path
import subprocess


def test_shared_changes_propagate_and_project_overrides_win(tmp_path: Path) -> None:
    """Resolve fresh shared values while keeping project-only secrets local."""
    shared = tmp_path / ".env"
    project = tmp_path / "project"
    project.mkdir()
    shared.write_text("SHARED=before\nOVERRIDE=shared\n")
    local = project / ".env"
    local.write_text("OVERRIDE=project\nSECRET=fixture-secret\n")
    (project / "compose.yaml").write_text(
        "services:\n  example:\n    image: example\n    environment:\n"
        "      SHARED: ${SHARED}\n      OVERRIDE: ${OVERRIDE}\n"
        "      SECRET: ${SECRET}\n"
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("COMPOSE_")
    }
    environment["COMPOSE_ENV_FILES"] = f"{shared},.env"
    for value in ("before", "after"):
        shared.write_text(f"SHARED={value}\nOVERRIDE=shared\n")
        result = subprocess.run(
            ["docker", "compose", "config", "--format=json"],
            cwd=project,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        )
        resolved = json.loads(result.stdout)["services"]["example"]["environment"]
        assert resolved == {
            "SHARED": value,
            "OVERRIDE": "project",
            "SECRET": "fixture-secret",
        }
    assert local.read_text() == "OVERRIDE=project\nSECRET=fixture-secret\n"
