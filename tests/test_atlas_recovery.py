"""Exercise archive-only recovery without touching Docker or authority data."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "repair_oci", ROOT / "proxmox/atlas-repair-oci.py"
)
REPAIR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPAIR)


def archive(path: Path, files: dict[str, bytes]) -> None:
    """Write a tiny synthetic OCI archive with real content-addressed blobs."""
    with tarfile.open(path, "w:gz") as target:
        for name, raw in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(raw)
            target.addfile(member, io.BytesIO(raw))


def kit(path: Path, ipa: bool = False) -> dict[str, bytes]:
    """Create a kit mixing saved config IDs and manifest IDs, like production."""
    images = {}
    files = {
        "index.json": b'{"manifests": []}',
        "oci-layout": b'{"imageLayoutVersion":"1.0.0"}',
    }
    for number, role in enumerate(
        ("openvox", "vault", "unsealer") + (("ipa",) if ipa else ())
    ):
        config = json.dumps({"role": role}).encode()
        config_hash = hashlib.sha256(config).hexdigest()
        manifest = json.dumps(
            {
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "config": {"digest": "sha256:" + config_hash},
                "layers": [],
            }
        ).encode()
        manifest_hash = hashlib.sha256(manifest).hexdigest()
        files["blobs/sha256/" + config_hash] = config
        files["blobs/sha256/" + manifest_hash] = manifest
        images[role] = "sha256:" + (config_hash if number % 2 else manifest_hash)
    archive(path / "images.tar.gz", files)
    (path / "manifest.json").write_text(json.dumps({"images": images}))
    return files


@pytest.mark.parametrize("ipa", [False, True])
def test_repair_changes_only_index_and_keeps_real_manifests(
    tmp_path: Path, ipa: bool
) -> None:
    """Every role becomes importable while archived blobs remain byte-identical."""
    original = kit(tmp_path, ipa)
    stream = io.BytesIO()
    descriptors = REPAIR.repair(tmp_path, stream)
    with tarfile.open(fileobj=io.BytesIO(stream.getvalue())) as result:
        repaired = {member.name: result.extractfile(member).read() for member in result}
    assert {key: value for key, value in repaired.items() if key != "index.json"} == {
        key: value for key, value in original.items() if key != "index.json"
    }
    assert json.loads(repaired["index.json"])["manifests"] == list(descriptors.values())
    assert set(descriptors) == {"openvox", "vault", "unsealer"} | (
        {"ipa"} if ipa else set()
    )
    for role, descriptor in descriptors.items():
        blob = repaired["blobs/sha256/" + descriptor["digest"].removeprefix("sha256:")]
        assert descriptor["size"] == len(blob)
        assert (
            descriptor["annotations"]["io.containerd.image.name"]
            == f"docker.io/atlas-restore-oci/{role}:archived"
        )


@pytest.mark.parametrize("failure", ["missing", "corrupt", "duplicate", "index"])
def test_repair_refuses_incomplete_or_changed_images_before_output(
    tmp_path: Path, failure: str
) -> None:
    """Invalid image sets fail before anything can be sent to containerd."""
    files = kit(tmp_path)
    manifest_name = next(name for name, raw in files.items() if b'"layers"' in raw)
    if failure == "missing":
        del files[manifest_name]
    elif failure == "corrupt":
        files[manifest_name] += b" "
    elif failure == "duplicate":
        model = json.loads((tmp_path / "manifest.json").read_text())
        model["images"]["vault"] = model["images"]["openvox"]
        (tmp_path / "manifest.json").write_text(json.dumps(model))
    else:
        del files["index.json"]
    archive(tmp_path / "images.tar.gz", files)
    output = io.BytesIO()
    with pytest.raises(ValueError):
        REPAIR.repair(tmp_path, output)
    assert output.getvalue() == b""


def test_restore_requires_explicit_manifest_fingerprint() -> None:
    """A kit's own checksum cannot supply its independent integrity anchor."""
    result = subprocess.run(
        ["bash", str(ROOT / "proxmox/atlas-infra-restore.sh"), "/unused"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "EXPECTED_MANIFEST_SHA256" in result.stderr


@pytest.mark.parametrize("failure", ["fingerprint", "volume", "checkout", "mapping"])
def test_restore_refuses_existing_state_and_wrong_mapping(
    tmp_path: Path, failure: str
) -> None:
    """Refuse unsafe recovery before importing images or writing any volume."""
    kit_path = tmp_path / "kit"
    kit_path.mkdir()
    volumes = [
        "infra_openvox_" + name
        for name in ("ssl", "ca", "code", "data", "gems", "git", "r10k_cache", "trust")
    ]
    volumes += [
        "infra_vault_data",
        "infra_vault_logs",
        "infra_certificates",
        "infra_vault_unseal",
    ]
    archives = {}
    for name in volumes + [
        "credentials",
        "control",
        "control.git",
        "hardening",
        "images",
    ]:
        archive(kit_path / f"{name}.tar.gz", {})
    host_files = {
        "etc/docker/daemon.json": b'{"userns-remap":"default"}',
        "etc/subuid": b"dockremap:100000:65536\n",
        "etc/subgid": b"dockremap:100000:65536\n",
    }
    archive(kit_path / "host-config.tar.gz", host_files)
    for path in sorted(kit_path.glob("*.tar.gz")):
        archives[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (kit_path / "SHA256SUMS").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in archives.items())
    )
    manifest = kit_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "host": "atlas.home.arpa",
                "volume_count": 12,
                "root_sha256": "a" * 64,
                "archives": [
                    {"file": name, "sha256": digest}
                    for name, digest in archives.items()
                ],
            }
        )
    )
    fingerprint = hashlib.sha256(manifest.read_bytes()).hexdigest()
    if failure == "fingerprint":
        fingerprint = "0" * 64
    for name, raw in host_files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    if failure == "mapping":
        (tmp_path / "etc/subuid").write_text("dockremap:200000:65536\n")
    (tmp_path / "root").mkdir()
    source = tmp_path / "opt/docker/puppet-control-repo"
    if failure == "checkout":
        source.mkdir(parents=True)
    bins = tmp_path / "bin"
    bins.mkdir()
    tools = {
        "id": "echo 0",
        "hostname": "echo atlas.home.arpa",
        "docker": '[[ "$*" == "--host unix:///var/run/docker.sock volume ls --format {{.Name}}" ]] || exit 99\n'
        + ("echo infra_openvox_ssl" if failure == "volume" else ":"),
        "ctr": 'echo "image importer must not run" >&2; exit 99',
    }
    for name, body in tools.items():
        path = bins / name
        path.write_text("#!/bin/bash\nset -eu\n" + body + "\n")
        path.chmod(0o755)
    script_text = (ROOT / "proxmox/atlas-infra-restore.sh").read_text()
    for prefix in (
        "/root/openvox-recovery",
        "/opt/docker/",
        "/root/openvox-infra",
        "/opt/git/openvox/control.git",
    ):
        script_text = script_text.replace(prefix, str(tmp_path) + prefix)
    script_text = script_text.replace(
        " /etc/docker/daemon.json", f" {tmp_path}/etc/docker/daemon.json"
    )
    script_text = script_text.replace('"/etc/$name"', f'"{tmp_path}/etc/$name"')
    script = tmp_path / "restore.sh"
    script.write_text(script_text)
    result = subprocess.run(
        ["bash", str(script), str(kit_path), fingerprint],
        capture_output=True,
        text=True,
        env={"PATH": f"{bins}:/usr/bin:/bin"},
    )
    assert result.returncode != 0
    assert "image importer must not run" not in result.stderr
    if failure == "volume":
        assert "Refuse existing authority volume" in result.stderr
    elif failure == "checkout":
        assert "Refuse existing recovery target" in result.stderr
    elif failure == "mapping":
        assert "Restore saved dockremap subuid mapping" in result.stderr
