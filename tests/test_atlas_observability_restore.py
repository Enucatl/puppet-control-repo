"""Check snapshot restore boundaries with local SQLite and fake Docker volumes."""

import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess

import pytest


@pytest.mark.parametrize("mode", ["valid", "existing", "corrupt"])
def test_restore_requires_valid_database_and_new_volume(tmp_path, monkeypatch, mode):
    """Refuse replacement or corrupt state and retain a valid database's contents."""
    spec = importlib.util.spec_from_file_location(
        "restore", Path(__file__).parents[1] / "proxmox/atlas-observability-restore.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = tmp_path / "export"
    module.DOCKER_VOLUMES = tmp_path / "docker"
    module.VOLUMES = {"grafana": ("grafana.db",)}
    source = module.ROOT / "grafana/_data"
    source.mkdir(parents=True)
    database = source / "grafana.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE preserved (value TEXT)")
        db.execute("INSERT INTO preserved VALUES ('keep')")
    if mode == "corrupt":
        database.write_bytes(b"not a database")
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(module.socket, "getfqdn", lambda: "atlas.home.arpa")
    destination = module.DOCKER_VOLUMES / "grafana/_data"
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[2] == "create":
            destination.mkdir(parents=True)
        return subprocess.CompletedProcess(command, 0 if mode == "existing" else 1)

    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(
        module.subprocess,
        "check_output",
        lambda _: json.dumps([{"Mountpoint": str(destination)}]).encode(),
    )
    if mode != "valid":
        with pytest.raises((AssertionError, sqlite3.DatabaseError)):
            module.restore()
        assert source.exists() and not destination.exists()
        assert not any(c[2] == "create" for c in calls)
    else:
        module.restore()
        assert not source.exists()
        with sqlite3.connect(destination / "grafana.db") as db:
            assert db.execute("SELECT value FROM preserved").fetchall() == [("keep",)]
