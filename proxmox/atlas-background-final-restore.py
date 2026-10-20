#!/usr/bin/env python3
"""Install a background stack's final stopped-source volume, retaining staging."""

import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys

VOLUMES = {
    "protonmail-bridge": "protonmail-bridge_bridge-state",
    "rss-to-telegram-bot": "rss-to-telegram-bot_config",
    "tor-i2pd": "tor-i2pd_i2pd",
    "airflow": "airflow_postgres-outputs-volume",
}


def main():
    """Refuse active targets and retain their prior volume before final sync."""
    assert os.geteuid() == 0 and socket.getfqdn() == "atlas.home.arpa"
    assert len(sys.argv) == 2 and sys.argv[1] in VOLUMES
    stack = sys.argv[1]
    assert not subprocess.check_output(
        ["docker", "ps", "-q", "--filter", "label=com.docker.compose.project=" + stack]
    ).strip()
    name = VOLUMES[stack]
    root = Path("/root/openvox-infra/background-cutover") / stack
    source = root / "final-volumes" / name / "_data"
    backup = root / "before-final-sync"
    assert source.is_dir() and not source.is_symlink() and not backup.exists()
    for file in source.rglob("*"):
        if file.is_file() and file.suffix in (".db", ".sqlite3", ".session"):
            with file.open("rb") as stream:
                if stream.read(16) != b"SQLite format 3\x00":
                    continue
            with sqlite3.connect(file) as db:
                assert db.execute("PRAGMA quick_check").fetchall() == [("ok",)]
    volume = json.loads(subprocess.check_output(["docker", "volume", "inspect", name]))[
        0
    ]
    destination = Path(volume["Mountpoint"])
    assert destination == Path("/var/lib/docker/100000.100000/volumes") / name / "_data"
    assert destination.is_dir() and destination.stat().st_dev == source.stat().st_dev
    destination.rename(backup)
    source.rename(destination)
    print(f"{stack}: final state installed; previous Atlas staging retained")


if __name__ == "__main__":
    main()
