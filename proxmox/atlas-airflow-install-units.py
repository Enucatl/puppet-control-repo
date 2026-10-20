#!/usr/bin/env python3
"""Install Atlas Airflow schedules from the versioned node configuration."""

import os
from pathlib import Path
import socket
import subprocess

import yaml


def main():
    """Apply only Airflow's declared units and preserve the disabled analyst job."""
    assert os.geteuid() == 0 and socket.getfqdn() == "atlas.home.arpa"
    node = Path(__file__).with_name("atlas-node.yaml")
    units = yaml.safe_load(node.read_text())["profile::common::systemd::units"]
    names = {
        f"automation-{job}.{kind}"
        for job in ["exam", "cyber-analyst", "operations-analyst", "podcast-statistics"]
        for kind in ["service", "timer"]
    }
    assert names <= units.keys()
    for name in sorted(names):
        config = units[name]
        path = Path("/etc/systemd/system") / name
        path.write_text(config["content"])
        path.chmod(0o644)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    for name in sorted(names):
        config = units[name]
        if name.endswith(".timer"):
            action = "enable" if config["enable"] else "disable"
            subprocess.run(["systemctl", action, "--now", name], check=True)
            state = (
                subprocess.check_output(["systemctl", "is-active", name])
                .decode()
                .strip()
                if config["active"]
                else ""
            )
            assert not config["active"] or state == "active"
    print("Atlas Airflow units installed with their declared timer states")


if __name__ == "__main__":
    main()
