"""Provision a desktop VM on the local Proxmox host."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import random
import re
import shlex
import signal
from string import Template
import subprocess
import sys
import time

import niquests

from desktop_cloud_init import build_cloud_config


SCRIPT_DIR = Path(__file__).resolve().parent
SNIPPET_DIR = Path("/var/lib/vz/snippets")


def load_settings(env_file: Path = Path(".env")) -> dict[str, str]:
    """Read shared defaults and literal .env assignments without executing shell."""
    settings = dict(os.environ)
    for path in (SCRIPT_DIR / "config.sh", env_file):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            fields = shlex.split(line, comments=True)
            if fields[:1] == ["export"]:
                fields = fields[1:]
            if not fields:
                continue
            if len(fields) != 1 or not re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_]*=.*", fields[0]
            ):
                raise ValueError(f"{path}:{number}: expected KEY=value")
            key, value = fields[0].split("=", 1)
            if path == SCRIPT_DIR / "config.sh":
                value = Template(value).substitute(settings)
            settings[key] = value
    for key in ("VAULT_TOKEN", "VAULT_ADDR", "LOG_DIR"):
        if not settings.get(key):
            raise ValueError(f"{key} is not set")
    return settings


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Keep the desktop shell command's options and defaults."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-l", dest="letter", default="", metavar="LETTER")
    parser.add_argument("-i", dest="vmid", metavar="VMID")
    parser.add_argument("-c", dest="cores", type=int, default=8, metavar="CORES")
    parser.add_argument(
        "-m", dest="memory", type=int, default=8000, metavar="MEMORY_MB"
    )
    parser.add_argument("-d", dest="disk_size", default="128G", metavar="DISK_SIZE")
    args = parser.parse_args(argv)
    if args.letter and not re.fullmatch(r"[A-Za-z]", args.letter):
        parser.error("LETTER must be a single ASCII letter")
    if args.vmid is not None and not re.fullmatch(r"[1-9][0-9]*", args.vmid):
        parser.error("VMID must be a positive integer")
    if args.cores <= 0 or args.memory <= 0:
        parser.error("CORES and MEMORY_MB must be positive")
    if not re.fullmatch(r"\+?[0-9]+(?:\.[0-9]+)?[KMGT]?", args.disk_size):
        parser.error("DISK_SIZE must be a size such as 128G")
    return args


def pick_vm_name(letter: str) -> str:
    """Choose an alphabetic dictionary hostname, optionally by initial letter."""
    words = [
        word.lower()
        for word in Path("/usr/share/dict/words").read_text().splitlines()
        if re.fullmatch(r"[A-Za-z]+", word) and word.lower().startswith(letter.lower())
    ]
    if not words:
        raise ValueError("No dictionary words match the requested initial letter")
    return random.choice(words)


def create_vault_token(settings: dict[str, str], certname: str) -> str:
    """Issue and verify a single-use enrollment token without consuming it."""
    response = niquests.post(
        settings["VAULT_ADDR"].rstrip("/") + "/v1/auth/token/create-orphan",
        headers={"X-Vault-Token": settings["VAULT_TOKEN"]},
        json={
            "policies": ["puppet-enrollment"],
            "type": "service",
            "ttl": "2h",
            "num_uses": 1,
            "renewable": False,
            "no_default_policy": True,
            "meta": {"certname": certname},
        },
        verify=settings.get("VAULT_CACERT") or True,
        timeout=30,
        allow_redirects=False,
    )
    response.raise_for_status()
    data = response.json()
    auth = data.get("auth") if isinstance(data, dict) else None
    if not isinstance(auth, dict) or not (
        auth.get("policies") == ["puppet-enrollment"]
        and isinstance(auth.get("metadata"), dict)
        and auth["metadata"].get("certname") == certname
        and auth.get("token_type") == "service"
        and auth.get("orphan") is True
        and auth.get("renewable") is False
        and type(auth.get("num_uses")) is int
        and auth["num_uses"] == 1
        and type(auth.get("lease_duration")) in (int, float)
        and 0 < auth["lease_duration"] <= 7200
        and isinstance(auth.get("client_token"), str)
        and auth["client_token"]
    ):
        raise ValueError("Invalid Vault enrollment token response")
    return auth["client_token"]


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a host command with explicit arguments and capture its output."""
    return subprocess.run(list(args), check=check, text=True, capture_output=True)


def wait_for_cloudinit(vmid: str) -> None:
    """Wait for guest readiness and collect diagnostics on cloud-init failure."""
    print("Waiting for cloud-init to finish...", flush=True)
    while True:
        result = run(
            "qm", "guest", "exec", vmid, "--", "cloud-init", "status", check=False
        )
        if result.returncode == 0:
            status = json.loads(result.stdout).get("out-data", "")
            if "status: done" in status:
                return
            if "status: error" in status:
                for command in (
                    ("cloud-init", "status", "--long"),
                    ("journalctl", "-u", "cloud-init", "--no-pager", "-n", "80"),
                ):
                    diagnostic = run(
                        "qm", "guest", "exec", vmid, "--", *command, check=False
                    )
                    print(diagnostic.stdout or diagnostic.stderr, file=sys.stderr)
                raise RuntimeError("Cloud-init failed")
        time.sleep(10)


def provision(args: argparse.Namespace, settings: dict[str, str]) -> str:
    """Clone, configure, enroll, and reboot a desktop VM; clean up failed clones."""
    vmid = args.vmid or run("pvesh", "get", "/cluster/nextid").stdout.strip()
    if not re.fullmatch(r"[1-9][0-9]*", vmid) or vmid == settings["TEMPLATE_ID"]:
        raise ValueError("VMID must be positive and different from TEMPLATE_ID")
    name = pick_vm_name(args.letter)
    certname = f"{name}.{settings['DOMAIN_SUFFIX']}"
    storage = settings["DEFAULT_STORAGE"]
    snippet = SNIPPET_DIR / f"generated-desktop-{vmid}.yml"
    log_dir = Path(settings["LOG_DIR"])
    log_dir.mkdir(parents=True, exist_ok=True)
    print(f"Preparing {name} (VMID: {vmid})", flush=True)
    token = create_vault_token(settings, certname)
    config = build_cloud_config(
        token, certname, settings["PUPPET_SERVER"], settings["DOMAIN_SUFFIX"]
    )
    cloned = False
    try:
        with snippet.open("w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write("#cloud-config\n")
            json.dump(config, stream, indent=2)
            stream.write("\n")
        if run("qm", "status", vmid, check=False).returncode == 0:
            print(f"Replacing existing VM {vmid}...", flush=True)
            run("qm", "stop", vmid, check=False)
            run("qm", "destroy", vmid, "--purge")
        print(f"Cloning and configuring VM {vmid}...", flush=True)
        run(
            "qm",
            "clone",
            settings["TEMPLATE_ID"],
            vmid,
            "--name",
            name,
            "--storage",
            storage,
            "--full",
            "1",
        )
        cloned = True
        run("qm", "resize", vmid, "scsi0", args.disk_size)
        run(
            "qm",
            "set",
            vmid,
            "--agent",
            "1",
            "--cores",
            str(args.cores),
            "--memory",
            str(args.memory),
            "--cpu",
            "host",
        )
        run("qm", "set", vmid, "--machine", "q35", "--bios", "ovmf")
        run(
            "qm",
            "set",
            vmid,
            "--efidisk0",
            f"{storage}:0,efitype=4m,pre-enrolled-keys=1",
        )
        run(
            "qm",
            "set",
            vmid,
            "--cicustom",
            f"vendor={settings['SNIPPET_STORAGE']}:snippets/{snippet.name}",
            "--ipconfig0",
            "ip=dhcp",
        )
        run("qm", "start", vmid)
        wait_for_cloudinit(vmid)
        result = run(
            "qm", "guest", "exec", vmid, "--", "cat", "/var/log/cloud-init-output.log"
        )
        guest_log = json.loads(result.stdout)
        if guest_log.get("exitcode") != 0:
            raise RuntimeError("Could not read the guest cloud-init log")
        with (log_dir / "cloud-init-output.log").open("a") as stream:
            stream.write(guest_log["out-data"])
    except Exception, KeyboardInterrupt:
        if cloned:
            print(f"Provisioning failed; removing VM {vmid}...", file=sys.stderr)
            run("qm", "stop", vmid, check=False)
            run("qm", "destroy", vmid, "--purge", check=False)
        raise
    finally:
        snippet.unlink(missing_ok=True)
    print(f"Rebooting {name} to finalize the desktop...", flush=True)
    run("qm", "reboot", vmid)
    return name


def handle_termination(signum: int, frame: object) -> None:
    """Route SIGTERM through the same cleanup as an interrupted deployment."""
    raise KeyboardInterrupt


def main() -> int:
    """Run desktop provisioning and report failures without exposing credentials."""
    args = parse_args()
    signal.signal(signal.SIGTERM, handle_termination)
    try:
        name = provision(args, load_settings())
    except KeyboardInterrupt:
        print("Provisioning interrupted", file=sys.stderr)
        return 130
    except (
        OSError,
        ValueError,
        RuntimeError,
        subprocess.CalledProcessError,
        niquests.RequestException,
    ) as error:
        print(f"Error: {error}", file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError):
            print(error.stderr, file=sys.stderr)
        return 1
    print(f"VM Name: {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
