#!/usr/bin/env python3
"""Restore the remaining exported volumes using the guarded Atlas restore path."""

import importlib.util
import json
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "atlas_restore", Path(__file__).with_name("atlas-observability-restore.py")
)
assert SPEC and SPEC.loader
restore = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(restore)
STAGE = Path("/root/openvox-infra/stacks")


def main():
    """Validate copied SQLite stores and refuse any existing destination volume."""
    state = json.loads((STAGE / "source-state.json").read_text())
    restore.ROOT = STAGE / "volumes"
    restore.VOLUMES = dict.fromkeys(
        name for stack in state.values() for name in stack["volumes"]
    )
    for name in restore.VOLUMES:
        assert name and all(c.isalnum() or c in "_.-" for c in name)
        assert not name.startswith("infra_"), "Never restore legacy authority volumes"
        databases = []
        for path in (restore.ROOT / name / "_data").rglob("*"):
            if not path.is_file() or path.suffix not in (".db", ".sqlite3", ".sqlite"):
                continue
            with path.open("rb") as stream:
                if stream.read(16) != b"SQLite format 3\x00":
                    continue
            databases.append(str(path.relative_to(restore.ROOT / name / "_data")))
        restore.VOLUMES[name] = tuple(databases)
    consume = restore.ROOT / "opt/paperless-consume"
    assert consume.is_dir() and not Path("/opt/paperless-consume").exists()
    restore.restore()
    consume.rename("/opt/paperless-consume")
    print("Restored the isolated Paperless consume directory")


if __name__ == "__main__":
    main()
