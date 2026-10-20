#!/usr/bin/env python3
"""Move the Cortex GPU between stopped migration guests, retaining recovery data."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import socket
import subprocess


def qm(*args: str) -> str:
    """Run a Proxmox VM command, returning its standard output."""
    return subprocess.check_output(["qm", *args], text=True)


def config(vmid: str) -> dict[str, str]:
    """Read the complete current configuration for one VM."""
    return dict(line.split(": ", 1) for line in qm("config", vmid).splitlines())


def move(source: str, target: str, recovery: Path) -> None:
    """Transfer all PCI assignments only when both verified guests are stopped."""
    expected = {"200": "complex", "220": "cognition"}
    if {source, target} != set(expected):
        raise ValueError("Only Complex 200 and Cognition 220 can exchange the GPU")
    configurations = {vmid: config(vmid) for vmid in (source, target)}
    for vmid, values in configurations.items():
        if values.get("name") != expected[vmid]:
            raise ValueError(f"VM {vmid} has an unexpected identity")
        if qm("status", vmid).strip() != "status: stopped":
            raise ValueError(f"VM {vmid} must be stopped before GPU handover")
    pci = {
        key: value
        for key, value in configurations[source].items()
        if key.startswith("hostpci")
    }
    if not pci or any(key.startswith("hostpci") for key in configurations[target]):
        raise ValueError("Source must own PCI devices and target must have none")
    if "q35" not in configurations[target].get("machine", "") or (
        configurations[target].get("bios") != "ovmf"
    ):
        raise ValueError("Target must use q35 and OVMF for GPU passthrough")
    recovery.mkdir(mode=0o700, parents=True, exist_ok=True)
    record = recovery / f"gpu-{source}-to-{target}.json"
    if record.exists():
        raise ValueError(f"Retaining existing recovery record: {record}")
    with open(
        record, "x", opener=lambda path, flags: os.open(path, flags, 0o600)
    ) as stream:
        json.dump(configurations, stream, indent=2)
    options = [item for key, value in pci.items() for item in ("--" + key, value)]
    qm("set", source, "--delete", ",".join(pci))
    try:
        qm("set", target, *options)
    except subprocess.CalledProcessError:
        qm("set", source, *options)
        raise
    print(f"GPU moved from {source} to {target}; both VMs remain stopped")


def main() -> None:
    """Execute an explicit forward or rollback handover on Cortex."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("direction", choices=("to-cognition", "to-complex"))
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.getfqdn() != "proxmox-cortex.home.arpa":
        raise SystemExit("Run as root on proxmox-cortex.home.arpa")
    lock = open("/run/lock/cognition-gpu-handover.lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    source, target = (
        ("200", "220") if args.direction == "to-cognition" else ("220", "200")
    )
    move(source, target, Path("/root/cognition-migration"))


if __name__ == "__main__":
    main()
