"""Exercise isolated IPA restore and cleanup against real temporary archives."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import tarfile

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "proxmox/atlas-freeipa-rehearse.sh"
IMAGE = "sha256:" + "a" * 64
HELPER = "sha256:" + "b" * 64
LABEL = "ipa-migration"


@pytest.fixture
def rehearsal(tmp_path: Path) -> Path:
    """Prepare a checksummed original snapshot and a recording local Docker."""
    state = tmp_path / "root/openvox-infra/freeipa"
    (state / "container-inputs").mkdir(parents=True)
    (state.parent / "root.sha256").write_text("c" * 64)
    (state / "ready.json").write_text(
        json.dumps({"hostname": "ipa.home.arpa", "root_sha256": "c" * 64})
    )
    (state / "volume.json").write_text(json.dumps({"identity": "original-uuid"}))
    (state / "container-inputs/installer.json").write_text(
        json.dumps({"volume_identity": "original-uuid"})
    )
    for path in (state / "root.pem", state / "container-inputs/root.pem"):
        path.write_text("ROOT-CERTIFICATE")
    promoted = tmp_path / "etc/openvox-infra/promoted"
    promoted.parent.mkdir(parents=True)
    promoted.write_text("openvox-authority:atlas.home.arpa")
    (tmp_path / "run/lock").mkdir(parents=True)
    backup = tmp_path / "backup"
    backup.mkdir()
    original = tmp_path / "original"
    (original / "etc/ipa").mkdir(parents=True)
    for name, content in {
        ".atlas-freeipa-volume": "original-uuid\n",
        ".atlas-freeipa-installed": "original-uuid\n",
        "etc/ipa/default.conf": "realm = HOME.ARPA\n",
        "directory.db": "PRESERVED-PRIVATE-IDENTITIES",
    }.items():
        (original / name).write_text(content)
    subprocess.run(
        [
            "tar",
            "--numeric-owner",
            "-czf",
            str(backup / "infra_ipa_data.tar.gz"),
            "-C",
            str(original),
            ".",
        ],
        check=True,
    )
    checksum = hashlib.sha256(
        (backup / "infra_ipa_data.tar.gz").read_bytes()
    ).hexdigest()
    (backup / "SHA256SUMS").write_text(checksum + "  infra_ipa_data.tar.gz\n")
    (backup / "manifest.json").write_text(
        json.dumps(
            {
                "host": "atlas.home.arpa",
                "root_sha256": "c" * 64,
                "volume_count": 13,
                "images": {"ipa": IMAGE, "openvox": HELPER},
                "archives": [{"file": "infra_ipa_data.tar.gz", "sha256": checksum}],
            }
        )
    )
    (tmp_path / "resources.json").write_text("{}")
    bins = tmp_path / "bin"
    bins.mkdir()
    for name, response in (("id", "0"), ("hostname", "atlas.home.arpa")):
        path = bins / name
        path.write_text("#!/bin/bash\nprintf '%s\\n' " + shlex.quote(response))
        path.chmod(0o755)
    docker = bins / "docker"
    docker.write_text(
        "#!/usr/bin/env python3\n"
        + "ROOT = "
        + repr(str(tmp_path))
        + "\n"
        + r"""
import json
from pathlib import Path
import subprocess
import sys
root = Path(ROOT)
args = sys.argv[1:]
assert args[:2] == ["--host", "unix:///var/run/docker.sock"]
args = args[2:]
with (root / "commands.jsonl").open("a") as log:
    log.write(json.dumps(args) + "\n")
resources = json.loads((root / "resources.json").read_text())
def save():
    (root / "resources.json").write_text(json.dumps(resources))
if args[0] in ("container", "volume", "network"):
    kind, operation = args[:2]
    if operation == "ls":
        print("\n".join(k.split(":", 1)[1] for k in resources if k.startswith(kind + ":")))
    elif operation == "inspect":
        print(resources[kind + ":" + args[-1]])
    elif operation == "create":
        name = args[-1]
        assert "--label" in args and args[args.index("--label") + 1] == "home.arpa.rehearsal=ipa-migration"
        assert kind + ":" + name not in resources
        resources[kind + ":" + name] = "ipa-migration"
        if kind == "volume":
            (root / "volumes" / name).mkdir(parents=True)
        save()
    elif operation == "rm":
        assert resources.pop(kind + ":" + args[-1]) == "ipa-migration"
        save()
    else:
        raise AssertionError(args)
elif args[:2] == ["image", "inspect"]:
    assert all(value.startswith("sha256:") for value in args[2:])
elif args[0] == "run":
    assert "--pull" in args and args[args.index("--pull") + 1] == "never"
    assert not any(value in args for value in ("--publish", "-p", "--privileged", "--userns=host"))
    mount = next(value for value in args if value.startswith("type=volume,"))
    volume = mount.split("src=", 1)[1].split(",", 1)[0]
    assert volume == "atlas-ipa-rehearsal-data"
    path = root / "volumes" / volume
    if "--entrypoint" in args:
        entrypoint = args[args.index("--entrypoint") + 1]
        if entrypoint == "/bin/tar":
            subprocess.run(["tar", "--numeric-owner", "--acls", "--xattrs", "-xzf", "-", "-C", str(path)], check=True)
        elif entrypoint == "/bin/sh":
            command = args[args.index("-ec") + 1].replace("/volume", str(path))
            subprocess.run(["sh", "-ec", command, "sh", args[-1]], check=True)
        else:
            raise AssertionError(args)
    else:
        assert args[-1] == "sha256:" + "a" * 64
        assert args[args.index("--runtime") + 1] == "sysbox-runc"
        assert args[args.index("--network") + 1] == "atlas-ipa-rehearsal"
        assert args[args.index("--add-host") + 1] == "ipa.home.arpa:127.0.0.1"
        assert "IPA_SERVER_IP=no-update" in args
        assert (path / "directory.db").read_text() == "PRESERVED-PRIVATE-IDENTITIES"
        assert "dst=/bootstrap,readonly" in " ".join(args)
        resources["container:atlas-ipa-rehearsal"] = "ipa-migration"
        save()
elif args[0] == "exec":
    assert args[1] == "atlas-ipa-rehearsal"
    if (root / "fail-readiness").exists():
        sys.exit(42)
else:
    raise AssertionError(args)
"""
    )
    docker.chmod(0o755)
    script = SCRIPT.read_text()
    for prefix in ("/etc/openvox-infra", "/root/openvox-infra", "/run/lock"):
        script = script.replace(prefix, str(tmp_path) + prefix)
    (tmp_path / "rehearse.sh").write_text(script)
    return tmp_path


def run(root: Path, action: str = "backup") -> subprocess.CompletedProcess[str]:
    """Run only temporary resources with unsafe ambient Docker settings."""
    return subprocess.run(
        [
            "bash",
            str(root / "rehearse.sh"),
            str(root / "backup") if action == "backup" else action,
        ],
        env={
            **os.environ,
            "PATH": f"{root / 'bin'}:{os.environ['PATH']}",
            "DOCKER_HOST": "tcp://unrelated:2375",
        },
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_restore_and_explicit_cleanup_preserve_snapshot_identity(
    rehearsal: Path,
) -> None:
    """The clone gets original files and exact image, with no production mounts."""
    result = run(rehearsal)
    assert result.returncode == 0, result.stderr
    restored = rehearsal / "volumes/atlas-ipa-rehearsal-data"
    assert (restored / ".atlas-freeipa-installed").read_text() == "original-uuid\n"
    assert (restored / "directory.db").read_text() == "PRESERVED-PRIVATE-IDENTITIES"
    with tarfile.open(rehearsal / "backup/infra_ipa_data.tar.gz") as archive:
        assert (restored / "directory.db").stat().st_uid == archive.getmember(
            "./directory.db"
        ).uid
    assert "PRESERVED-PRIVATE-IDENTITIES" not in result.stdout + result.stderr
    commands = [
        json.loads(line)
        for line in (rehearsal / "commands.jsonl").read_text().splitlines()
    ]
    assert not any("infra_ipa_data" in value for args in commands for value in args)
    assert not any("--internal" in args for args in commands)
    assert run(rehearsal).returncode != 0
    assert run(rehearsal, "cleanup").returncode == 0
    assert json.loads((rehearsal / "resources.json").read_text()) == {}


@pytest.mark.parametrize(
    "failure",
    [
        "checksum",
        "unlisted",
        "root",
        "identity",
        "installed",
        "unlabelled",
        "readiness",
    ],
)
def test_failed_preflight_and_cleanup_are_bounded(
    rehearsal: Path, failure: str
) -> None:
    """Refuse changed identity or occupied names; retain failed clones for inspection."""
    if failure == "checksum":
        with (rehearsal / "backup/infra_ipa_data.tar.gz").open("ab") as stream:
            stream.write(b"changed")
    elif failure == "unlisted":
        digest = hashlib.sha256(
            (rehearsal / "backup/manifest.json").read_bytes()
        ).hexdigest()
        (rehearsal / "backup/SHA256SUMS").write_text(digest + "  manifest.json\n")
    elif failure == "root":
        (rehearsal / "root/openvox-infra/root.sha256").write_text("d" * 64)
    elif failure == "identity":
        (rehearsal / "root/openvox-infra/freeipa/volume.json").write_text(
            json.dumps({"identity": "replacement"})
        )
    elif failure == "installed":
        (rehearsal / "original/.atlas-freeipa-installed").unlink()
        archive = rehearsal / "backup/infra_ipa_data.tar.gz"
        subprocess.run(
            ["tar", "-czf", str(archive), "-C", str(rehearsal / "original"), "."],
            check=True,
        )
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        (rehearsal / "backup/SHA256SUMS").write_text(
            digest + "  infra_ipa_data.tar.gz\n"
        )
        manifest_path = rehearsal / "backup/manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["archives"][0]["sha256"] = digest
        manifest_path.write_text(json.dumps(manifest))
    elif failure == "unlabelled":
        (rehearsal / "resources.json").write_text(
            json.dumps({"volume:atlas-ipa-rehearsal-data": "production"})
        )
    elif failure == "readiness":
        (rehearsal / "fail-readiness").touch()
    result = run(rehearsal)
    assert result.returncode != 0
    assert "clone ready" not in result.stdout
    resources = json.loads((rehearsal / "resources.json").read_text())
    if failure in ("checksum", "unlisted", "root", "identity"):
        assert resources == {}
    if failure == "installed":
        assert set(resources) == {
            "volume:atlas-ipa-rehearsal-data",
            "network:atlas-ipa-rehearsal",
        }
    if failure == "unlabelled":
        assert run(rehearsal, "cleanup").returncode != 0
        assert json.loads((rehearsal / "resources.json").read_text()) == resources
    else:
        assert run(rehearsal, "cleanup").returncode == 0
        assert json.loads((rehearsal / "resources.json").read_text()) == {}
