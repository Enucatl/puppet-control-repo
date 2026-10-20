#!/usr/bin/env python3
"""Copy Complex's persistent state to an idle Cognition without starting stacks."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess

SOURCE = "user_l@complex.home.arpa"
TREES = ("/opt/docker", "/home", "/etc/wolf", "/var/lib/wolf")
RECOVERY_TREES = ("/etc", "/opt/vault", "/opt/gow", "/var/backups", "/var/spool")


def remote(*args: str) -> str:
    """Read source state through SSH without printing captured secrets."""
    return subprocess.check_output(
        [
            "ssh",
            "-oBatchMode=yes",
            "-oStrictHostKeyChecking=yes",
            SOURCE,
            shlex.join(("sudo", "-n", *args)),
        ],
        text=True,
    )


def check_idle(final: bool) -> None:
    """Refuse target writers and require a quiesced source for the final copy."""
    if subprocess.check_output(["docker", "ps", "-q"], text=True).strip():
        raise ValueError("Stop all Cognition containers before copying state")
    if final and remote("docker", "ps", "-q").strip():
        raise ValueError("Stop all Complex containers before the final copy")
    source_security = json.loads(
        remote("docker", "info", "--format", "{{json .SecurityOptions}}")
    )
    target_security = json.loads(
        subprocess.check_output(
            ["docker", "info", "--format", "{{json .SecurityOptions}}"], text=True
        )
    )
    if ("name=userns" in source_security) != ("name=userns" in target_security):
        raise ValueError(
            "Docker user namespaces differ; plan container data ownership conversion before copying"
        )
    if "name=userns" in source_security:
        for filename in ("/etc/subuid", "/etc/subgid"):
            source = remote("cat", filename).splitlines()
            target = Path(filename).read_text().splitlines()
            if [line for line in source if line.startswith("dockremap:")] != [
                line for line in target if line.startswith("dockremap:")
            ]:
                raise ValueError(f"Docker namespace mappings differ in {filename}")


def sync(source: str, target: Path, final: bool) -> None:
    """Preserve numeric ownership, links, ACLs and xattrs inside one filesystem."""
    if any(path.is_symlink() for path in (target, *target.parents)):
        raise ValueError(f"Refusing symlink destination {target}")
    target.mkdir(parents=True, exist_ok=True)
    command = [
        "rsync",
        "-aHAXxz",
        "--skip-compress=",
        "--sparse",
        "--numeric-ids",
        "--protect-args",
    ]
    if source == "/home":
        command.append("--exclude=/*/.config/home-arpa/mtls/")
        command.append("--exclude=/user_l/.ssh/")
    if source == "/etc":
        command.append("--exclude=/wolf/")
    if final:
        command.append("--delete")
    print(f"Copying {source} to {target}", flush=True)
    subprocess.run(
        command
        + [
            "--rsync-path=sudo -n rsync",
            "-e",
            "ssh -oBatchMode=yes -oStrictHostKeyChecking=yes",
            f"{SOURCE}:{source}/",
            str(target) + "/",
        ],
        check=True,
    )


def main() -> None:
    """Copy known persistent roots and every local named volume without Docker metadata."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--final", action="store_true", help="Require stopped source containers"
    )
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.getfqdn() != "cognition.home.arpa":
        raise SystemExit("Run as root on cognition.home.arpa")
    lock = open("/run/lock/cognition-copy-state.lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    check_idle(args.final)
    for path in (
        Path("/root/cognition-migration"),
        Path("/root/cognition-migration/source-system"),
    ):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.chmod(0o700)
    containers = remote("docker", "ps", "-aq").splitlines()
    inventory = (
        json.loads(remote("docker", "inspect", *containers)) if containers else []
    )
    for container in inventory:
        for mount in container.get("Mounts", []):
            if mount["Type"] != "bind":
                continue
            path = Path(mount["Source"])
            if any(path.is_relative_to(root) for root in TREES):
                continue
            if any(
                path.is_relative_to(root)
                for root in ("/dev", "/run", "/var/run", "/etc/ssl", "/export/scratch")
            ) or str(path) in ("/etc/localtime", "/etc/timezone"):
                continue
            raise ValueError(
                f"Inventory requires a separate bind-mount migration: {path}"
            )
    names = remote("docker", "volume", "ls", "-q").splitlines()
    volumes = json.loads(remote("docker", "volume", "inspect", *names)) if names else []
    for volume in volumes:
        if volume["Driver"] != "local" or volume.get("Options"):
            raise ValueError(
                f"Volume {volume['Name']} requires a separate storage migration"
            )
    for source in TREES:
        if remote(
            "sh", "-c", f"test ! -d {shlex.quote(source)} || echo present"
        ).strip():
            sync(source, Path(source), args.final)
    sync("/root", Path("/root/cognition-migration/source-root"), args.final)
    for source in RECOVERY_TREES:
        if remote(
            "sh", "-c", f"test ! -d {shlex.quote(source)} || echo present"
        ).strip():
            sync(
                source,
                Path("/root/cognition-migration/source-system") / source.lstrip("/"),
                args.final,
            )
    if remote("sh", "-c", "test ! -d /home/user_l/.ssh || echo present").strip():
        sync(
            "/home/user_l/.ssh",
            Path("/root/cognition-migration/source-admin-ssh"),
            args.final,
        )
    for volume in volumes:
        name = volume["Name"]
        subprocess.run(
            ["docker", "volume", "create", name], check=True, stdout=subprocess.DEVNULL
        )
        target = json.loads(
            subprocess.check_output(["docker", "volume", "inspect", name])
        )[0]
        if target["Driver"] != "local" or target.get("Options"):
            raise ValueError(f"Target volume {name} uses external storage")
        sync(volume["Mountpoint"], Path(target["Mountpoint"]), args.final)
    print(
        "State copied; no containers started. Review endpoints and external bind mounts before cutover."
    )


if __name__ == "__main__":
    main()
