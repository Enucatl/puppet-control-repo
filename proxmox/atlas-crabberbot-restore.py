#!/usr/bin/env python3
"""Replace staged Crabberbot state with the final stopped-source copy."""

import json
import os
from pathlib import Path
import socket
import subprocess

ROOT = Path("/root/openvox-infra/crabberbot-cutover")
VOLUMES = (
    "crabberbot_pgdata",
    "crabberbot_downloads",
    "crabberbot_telegram-bot-api-data",
    "crabberbot_downloader_socket",
)


def main():
    """Keep the pre-cutover Atlas copy and move final data into inactive volumes."""
    assert os.geteuid() == 0 and socket.getfqdn() == "atlas.home.arpa"
    assert not subprocess.check_output(
        [
            "docker",
            "ps",
            "-q",
            "--filter",
            "label=com.docker.compose.project=crabberbot",
        ]
    ).strip()
    backup = ROOT / "before-final-sync"
    assert not backup.exists(), (
        "Final state has already been installed or needs recovery"
    )
    moves = []
    for name in VOLUMES:
        source = ROOT / "final-volumes" / name / "_data"
        assert source.is_dir() and not source.is_symlink()
        volume = json.loads(
            subprocess.check_output(["docker", "volume", "inspect", name])
        )[0]
        destination = Path(volume["Mountpoint"])
        assert (
            destination
            == Path("/var/lib/docker/100000.100000/volumes") / name / "_data"
        )
        assert (
            destination.is_dir() and source.stat().st_dev == destination.stat().st_dev
        )
        moves.append((source, destination, backup / name))
    database = ROOT / "final-volumes/crabberbot_pgdata/_data"
    assert (database / "18/docker/PG_VERSION").read_text().strip() == "18"
    assert not (database / "18/docker/postmaster.pid").exists(), (
        "Source PostgreSQL was not stopped cleanly"
    )
    backup.mkdir(mode=0o700)
    for source, destination, saved in moves:
        destination.rename(saved)
        source.rename(destination)
    print("Final Crabberbot state installed; previous Atlas state preserved")


if __name__ == "__main__":
    main()
