"""Provision a desktop VM on the local Proxmox host."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import random
import re
import shlex
import signal
from string import Template
import subprocess
import sys
import time
from urllib.parse import unquote

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
    for key in ("VAULT_TOKEN", "VAULT_ADDR", "LOG_DIR", "OPENVOX_CA_CERT"):
        if not settings.get(key):
            raise ValueError(f"{key} is not set")
    return settings


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Keep the desktop shell command's options and defaults."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-l", dest="letter", default="", metavar="LETTER")
    parser.add_argument("-i", dest="vmid", metavar="VMID")
    parser.add_argument("-n", dest="name", metavar="HOSTNAME")
    parser.add_argument("-t", dest="template_id", metavar="TEMPLATE_ID")
    parser.add_argument("--puppet-server", metavar="HOSTNAME")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--keep-failed", action="store_true")
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
    for value in (args.name, args.puppet_server):
        if value is not None and (
            len(value) > 253
            or any(
                len(label) > 63
                or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label)
                for label in value.split(".")
            )
        ):
            parser.error("HOSTNAME must contain valid DNS labels")
    if args.name and "." in args.name:
        parser.error("-n requires a short hostname without the domain suffix")
    if args.template_id is not None and not re.fullmatch(
        r"[1-9][0-9]*", args.template_id
    ):
        parser.error("TEMPLATE_ID must be a positive integer")
    if args.name and args.letter:
        parser.error("-n and -l cannot be combined")
    if args.rebuild and not (args.vmid and args.name):
        parser.error("--rebuild requires explicit -i VMID and -n HOSTNAME")
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
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
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
    raise RuntimeError("Timed out waiting for cloud-init/QEMU guest agent")


def require_owned_vm(vmid: str, name: str, marker: str) -> None:
    """Refuse destructive operations unless the local VM has our exact identity."""
    result = run("qm", "config", vmid)
    config = dict(
        line.split(": ", 1) for line in result.stdout.splitlines() if ": " in line
    )
    if config.get("name") != name or unquote(config.get("description", "")) != marker:
        raise ValueError(f"VMID {vmid} is not the expected script-owned VM {name}")
    if config.get("template") == "1":
        raise ValueError(f"VMID {vmid} is a template")


def save_failure_diagnostics(vmid: str, log_dir: Path) -> None:
    """Preserve private guest diagnostics before deleting a failed clone."""
    destination = log_dir / f"desktop-{vmid}-failure-{time.time_ns()}"
    destination.mkdir(mode=0o700)
    for name, command in (
        ("cloud-init-output", ("cat", "/var/log/cloud-init-output.log")),
        (
            "puppet-report",
            ("cat", "/opt/puppetlabs/puppet/cache/state/last_run_report.yaml"),
        ),
        ("cloud-init", ("tail", "-n", "100", "/var/log/cloud-init.log")),
        ("cloud-final", ("journalctl", "-u", "cloud-final", "--no-pager", "-n", "100")),
    ):
        result = run("qm", "guest", "exec", vmid, "--", *command, check=False)
        with (destination / f"{name}.json").open("w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(
                {
                    "status": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                },
                stream,
            )
    print(f"Private failure diagnostics: {destination}", file=sys.stderr)


def verify_guest_hostname(vmid: str, name: str, certname: str) -> None:
    """Check the guest's short and fully qualified names through its agent."""
    for command, expected in ((("hostname",), name), (("hostname", "-f"), certname)):
        result = run("qm", "guest", "exec", vmid, "--", *command)
        output = json.loads(result.stdout)
        if (
            output.get("exitcode") != 0
            or output.get("out-data", "").strip() != expected
        ):
            raise RuntimeError(f"Guest {' '.join(command)} does not equal {expected}")


def provision(args: argparse.Namespace, settings: dict[str, str]) -> str:
    """Clone, configure, enroll, and reboot a desktop VM; clean up failed clones."""
    vmid = args.vmid or run("pvesh", "get", "/cluster/nextid").stdout.strip()
    template_id = args.template_id or settings["TEMPLATE_ID"]
    if not re.fullmatch(r"[1-9][0-9]*", vmid) or vmid in {"9000", template_id}:
        raise ValueError(
            "VMID must be positive and different from protected template IDs"
        )
    name = args.name or pick_vm_name(args.letter)
    certname = f"{name}.{settings['DOMAIN_SUFFIX']}"
    marker = f"openvox-provisioning:desktop:{certname}"
    resources = json.loads(
        run(
            "pvesh",
            "get",
            "/cluster/resources",
            "--type",
            "vm",
            "--output-format",
            "json",
        ).stdout
    )
    if not isinstance(resources, list) or any(
        not isinstance(resource, dict)
        or type(resource.get("vmid")) is not int
        or resource["vmid"] <= 0
        for resource in resources
    ):
        raise ValueError("Invalid cluster VM inventory")
    if any(
        resource.get("name") == name and str(resource["vmid"]) != vmid
        for resource in resources
    ):
        raise ValueError(f"Hostname {name} is already allocated in the cluster")
    local_node = platform.node().split(".", 1)[0]
    existing = [resource for resource in resources if str(resource["vmid"]) == vmid]
    if existing:
        if (
            not args.rebuild
            or len(existing) != 1
            or existing[0].get("type") != "qemu"
            or existing[0].get("template")
            or existing[0].get("name") != name
            or existing[0].get("node") != local_node
        ):
            raise ValueError(f"VMID {vmid} is already in use; refusing to replace it")
        require_owned_vm(vmid, name, marker)
    sources = [
        resource for resource in resources if str(resource["vmid"]) == template_id
    ]
    if (
        len(sources) != 1
        or sources[0].get("type") != "qemu"
        or sources[0].get("node") != local_node
    ):
        raise ValueError("Source must be a local QEMU template")
    source_config = run("qm", "config", template_id).stdout.splitlines()
    if "template: 1" not in source_config:
        raise ValueError("Source must be a template")
    storage = settings["DEFAULT_STORAGE"]
    snippet = SNIPPET_DIR / f"generated-desktop-{vmid}.yml"
    log_dir = Path(settings["LOG_DIR"])
    log_dir.mkdir(parents=True, exist_ok=True)
    print(f"Preparing {name} (VMID: {vmid})", flush=True)
    ca_bundle = Path(settings["OPENVOX_CA_CERT"]).read_text()
    if "-----BEGIN CERTIFICATE-----" not in ca_bundle:
        raise ValueError("OPENVOX_CA_CERT must contain authenticated CA certificates")
    token = create_vault_token(settings, certname)
    config = build_cloud_config(
        token,
        certname,
        args.puppet_server or settings["PUPPET_SERVER"],
        settings["DOMAIN_SUFFIX"],
        ca_bundle,
    )
    cloned = False
    try:
        with snippet.open("w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write("#cloud-config\n")
            json.dump(config, stream, indent=2)
            stream.write("\n")
        if existing:
            require_owned_vm(vmid, name, marker)
            print(f"Replacing owned development VM {vmid}...", flush=True)
            run("qm", "stop", vmid)
            run("qm", "destroy", vmid, "--purge")
        print(f"Cloning and configuring VM {vmid}...", flush=True)
        run(
            "qm",
            "clone",
            template_id,
            vmid,
            "--name",
            name,
            "--storage",
            storage,
            "--full",
            "1",
            "--description",
            marker,
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
        run("qm", "set", vmid, "--machine", "q35", "--bios", "ovmf", "--vga", "virtio")
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
            "--searchdomain",
            settings["DOMAIN_SUFFIX"],
        )
        run("qm", "start", vmid)
        wait_for_cloudinit(vmid)
        verify_guest_hostname(vmid, name, certname)
        result = run(
            "qm", "guest", "exec", vmid, "--", "cat", "/var/log/cloud-init-output.log"
        )
        guest_log = json.loads(result.stdout)
        if guest_log.get("exitcode") != 0:
            raise RuntimeError("Could not read the guest cloud-init log")
        with (log_dir / "cloud-init-output.log").open("a") as stream:
            stream.write(guest_log["out-data"])
        run("qm", "set", vmid, "--delete", "cicustom")
    except Exception, KeyboardInterrupt:
        if cloned:
            try:
                save_failure_diagnostics(vmid, log_dir)
            except (OSError, subprocess.SubprocessError) as diagnostic_error:
                print(
                    f"Could not save failure diagnostics: {diagnostic_error}",
                    file=sys.stderr,
                )
            try:
                require_owned_vm(vmid, name, marker)
                if args.keep_failed:
                    run("qm", "set", vmid, "--delete", "cicustom")
                    print(
                        f"Provisioning failed; retaining VM {vmid} for repair.",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"Provisioning failed; removing VM {vmid}...", file=sys.stderr
                    )
                    run("qm", "stop", vmid)
                    run("qm", "destroy", vmid, "--purge")
            except (ValueError, subprocess.CalledProcessError) as cleanup_error:
                print(f"VM cleanup refused or failed: {cleanup_error}", file=sys.stderr)
        raise
    finally:
        snippet.unlink(missing_ok=True)
    print(f"Rebooting {name} to finalize the desktop...", flush=True)
    boot_id_command = (
        "qm",
        "guest",
        "exec",
        vmid,
        "--",
        "cat",
        "/proc/sys/kernel/random/boot_id",
    )
    boot_id = json.loads(run(*boot_id_command).stdout).get("out-data")
    if not boot_id:
        raise RuntimeError("Could not read the guest boot ID")
    run("qm", "reboot", vmid)
    for _ in range(60):
        time.sleep(5)
        result = run(*boot_id_command, check=False)
        if result.returncode == 0:
            rebooted = json.loads(result.stdout)
            if rebooted.get("exitcode") == 0 and rebooted.get("out-data") not in (
                None,
                "",
                boot_id,
            ):
                break
    else:
        raise RuntimeError("Guest did not reboot within five minutes")
    wait_for_cloudinit(vmid)
    verify_guest_hostname(vmid, name, certname)
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
