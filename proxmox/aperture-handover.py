#!/usr/bin/env python3
"""Save, transfer, or restore the physical desktop on proxmox.home.arpa."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import socket
import subprocess

RECOVERY = Path("/root/aperture-cutover")
IDENTITIES = {
    "300": ("forbearance", "uuid=3dea96c4-ea4b-439a-aff4-4c439daaaafb"),
    "310": ("aperture", "uuid=10c36fa0-e3ba-42bc-a7e3-ba87b907ff55"),
}
PCI = {
    "hostpci0": "0000:09:00.0,pcie=1,x-vga=1",
    "hostpci1": "0000:09:00.1,pcie=1",
    "hostpci2": "0000:09:00.3,pcie=1,rombar=0",
    "hostpci3": "0000:09:00.4,pcie=1,rombar=0",
}
SETTINGS = {"memory", "onboot", "startup", "vga"}


def qm(*args: str) -> str:
    """Run a checked Proxmox command."""
    return subprocess.check_output(["qm", *args], text=True)


def config(vmid: str) -> dict[str, str]:
    """Read a guest configuration without accepting pending changes."""
    pending = json.loads(
        subprocess.check_output(
            [
                "pvesh",
                "get",
                f"/nodes/proxmox/qemu/{vmid}/pending",
                "--output-format",
                "json",
            ],
            text=True,
        )
    )
    if any("pending" in entry or entry.get("delete") for entry in pending):
        raise ValueError(f"VM {vmid} has pending changes")
    return dict(line.split(": ", 1) for line in qm("config", vmid).splitlines())


def pci(values: dict[str, str]) -> dict[str, str]:
    """Select every configured PCI passthrough assignment."""
    return {key: value for key, value in values.items() if key.startswith("hostpci")}


def inspect() -> dict[str, dict[str, str]]:
    """Reject unexpected identities, locks, firmware, or device co-owners."""
    configs = {vmid: config(vmid) for vmid in IDENTITIES}
    for vmid, (name, uuid) in IDENTITIES.items():
        values = configs[vmid]
        if (values.get("name"), values.get("smbios1")) != (name, uuid):
            raise ValueError(f"Unexpected identity for VM {vmid}")
        if "lock" in values:
            raise ValueError(f"VM {vmid} is locked")
        if any(key.startswith("usb") for key in values):
            raise ValueError("Unexpected USB passthrough; inspect before proceeding")
    if configs["310"].get("bios") != "ovmf" or "q35" not in configs["310"].get(
        "machine", ""
    ):
        raise ValueError("Aperture requires OVMF and q35")
    for path in Path("/etc/pve/qemu-server").glob("*.conf"):
        if path.stem not in IDENTITIES:
            for line in path.read_text().splitlines():
                if line.startswith("hostpci") and "09:00" in line:
                    raise ValueError(f"Desktop PCI device also configured in {path}")
    return configs


def stopped() -> None:
    """Require both guests stopped before changing any configuration."""
    for vmid in IDENTITIES:
        if qm("status", vmid).strip() != "status: stopped":
            raise ValueError(f"VM {vmid} must be stopped; use graceful shutdown first")


def set_values(vmid: str, desired: dict[str, str], current: dict[str, str]) -> None:
    """Restore only the desktop settings, preserving every unrelated VM option."""
    keys = SETTINGS | set(PCI)
    delete = sorted((keys & current.keys()) - desired.keys())
    args = [
        item
        for key in sorted(keys & desired.keys())
        for item in ("--" + key, desired[key])
    ]
    if delete:
        args += ["--delete", ",".join(delete)]
    qm("set", vmid, *args)


def handover(action: str, recovery: Path = RECOVERY) -> None:
    """Perform a guarded preparation, stopped transfer, or saved-state rollback."""
    configs = inspect()
    record = recovery / "before.json"
    if action == "prepare":
        if pci(configs["300"]) != PCI or pci(configs["310"]):
            raise ValueError(
                "Initial PCI ownership differs from the reviewed assignments"
            )
        if (
            configs["300"].get("memory") != "16384"
            or configs["310"].get("memory") != "8192"
        ):
            raise ValueError("Unexpected initial memory allocation")
        recovery.mkdir(mode=0o700, parents=True, exist_ok=True)
        with open(
            record, "x", opener=lambda path, flags: os.open(path, flags, 0o600)
        ) as stream:
            json.dump(configs, stream, indent=2)
        for vmid in IDENTITIES:
            (recovery / f"{vmid}.conf").write_text(qm("config", vmid))
        print(f"Saved original configurations in {record}; guests unchanged")
        return
    saved = json.loads(record.read_text())
    stopped()
    for vmid in IDENTITIES:
        fixed = lambda values: {
            key: value
            for key, value in values.items()
            if key not in SETTINGS and not key.startswith("hostpci")
        }
        if fixed(configs[vmid]) != fixed(saved[vmid]):
            raise ValueError(f"Unrelated configuration changed for VM {vmid}")
        if any(
            key not in PCI or value != PCI[key]
            for key, value in pci(configs[vmid]).items()
        ):
            raise ValueError(f"Unexpected PCI assignment for VM {vmid}")
    if action == "apply":
        if configs != saved or pci(saved["300"]) != PCI or pci(saved["310"]):
            raise ValueError(
                "Current configuration differs from saved initial configuration"
            )
        source = {key: value for key, value in saved["300"].items() if key not in PCI}
        source.update(memory="4096", onboot="0")
        target = (
            saved["310"]
            | PCI
            | {"memory": "16384", "onboot": "1", "startup": "order=3", "vga": "none"}
        )
        set_values("300", source, configs["300"])
        set_values("310", target, configs["310"])
        expected = {"300": source, "310": target}
    else:
        # Remove Aperture's hardware before restoring any Forbearance assignment.
        set_values("310", saved["310"], configs["310"])
        set_values("300", saved["300"], configs["300"])
        expected = saved
    if inspect() != expected:
        raise ValueError("Configuration verification failed; leave both guests stopped")
    print(f"{action} complete; both guests remain stopped")


def main() -> None:
    """Run only as root on the intended hypervisor under an exclusive lock."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "apply", "rollback"))
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.getfqdn() != "proxmox.home.arpa":
        raise SystemExit("Run as root on proxmox.home.arpa")
    os.umask(0o077)
    with open("/run/lock/aperture-handover.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        handover(args.action)


if __name__ == "__main__":
    main()
