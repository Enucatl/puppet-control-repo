#!/usr/bin/env python3
"""Install the exported snapshot into new, empty Atlas Docker volumes."""

import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess

ROOT = Path("/root/openvox-infra/observability/volumes")
DOCKER_VOLUMES = Path("/var/lib/docker/100000.100000/volumes")
VOLUMES = {
    "grafana-loki_grafana_data": ("grafana.db",),
    "grafana-loki_loki_data": (),
    "beszel_beszel_data": ("data.db", "auxiliary.db"),
}


def restore():
    """Validate every input before moving data; refuse existing Docker volumes."""
    assert os.geteuid() == 0 and socket.getfqdn() == "atlas.home.arpa"
    for name, databases in VOLUMES.items():
        source = ROOT / name / "_data"
        assert source.is_dir() and not source.is_symlink()
        exists = subprocess.run(
            ["docker", "volume", "inspect", name], capture_output=True
        )
        assert exists.returncode != 0, f"Refusing existing volume {name}"
        for filename in databases:
            assert (source / filename).is_file()
            with sqlite3.connect(source / filename) as db:
                assert db.execute("PRAGMA quick_check").fetchall() == [("ok",)]
    for name in VOLUMES:
        subprocess.run(
            ["docker", "volume", "create", name], check=True, stdout=subprocess.DEVNULL
        )
        config = json.loads(
            subprocess.check_output(["docker", "volume", "inspect", name])
        )[0]
        destination = Path(config["Mountpoint"])
        assert destination == DOCKER_VOLUMES / name / "_data"
        destination.rmdir()
        (ROOT / name / "_data").rename(destination)
        print(f"Restored {name}")


if __name__ == "__main__":
    restore()
