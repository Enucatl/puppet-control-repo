#!/usr/bin/env python3
"""Expose every genuine archived authority manifest to containerd's OCI importer."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import tarfile
from typing import BinaryIO


MEDIA_TYPES = {
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
}


def repair(kit: Path, output: BinaryIO) -> dict:
    """Replace only the archive index, keeping original manifests and layer bytes."""
    wanted = json.loads((kit / "manifest.json").read_text())["images"]
    if set(wanted) not in (
        {"openvox", "vault", "unsealer"},
        {"openvox", "vault", "unsealer", "ipa"},
    ):
        raise ValueError("Expected the established Atlas image roles")
    if len(set(wanted.values())) != len(wanted) or any(
        not re.fullmatch(r"sha256:[0-9a-f]{64}", digest) for digest in wanted.values()
    ):
        raise ValueError("Expected distinct immutable image IDs")
    found = {}
    image = kit / "images.tar.gz"
    indexes = 0
    with tarfile.open(image, "r|gz") as source:
        for member in source:
            if member.name == "index.json":
                indexes += 1
            if (
                not member.isfile()
                or member.size > 1024 * 1024
                or not re.fullmatch(r"blobs/sha256/[0-9a-f]{64}", member.name)
            ):
                continue
            raw = source.extractfile(member).read()
            try:
                item = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(item, dict) or item.get("mediaType") not in MEDIA_TYPES:
                continue
            digest = "sha256:" + member.name.rsplit("/", 1)[1]
            roles = [
                role
                for role, expected in wanted.items()
                if expected in (digest, item.get("config", {}).get("digest"))
            ]
            if not roles:
                continue
            if len(roles) != 1 or roles[0] in found:
                raise ValueError("Ambiguous archived image manifest")
            if hashlib.sha256(raw).hexdigest() != digest.removeprefix("sha256:"):
                raise ValueError("Archived image manifest digest mismatch")
            role = roles[0]
            found[role] = {
                "mediaType": item["mediaType"],
                "digest": digest,
                "size": member.size,
                "annotations": {
                    "io.containerd.image.name": f"docker.io/atlas-restore-oci/{role}:archived",
                    "org.opencontainers.image.ref.name": "archived",
                },
            }
    if indexes != 1:
        raise ValueError("Expected one OCI index")
    if set(found) != set(wanted):
        raise ValueError("Missing archived authority image manifests")
    index = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": list(found.values()),
        }
    ).encode()
    replaced = False
    with (
        tarfile.open(image, "r|gz") as source,
        tarfile.open(fileobj=output, mode="w|") as target,
    ):
        for member in source:
            if member.name == "index.json":
                if replaced:
                    raise ValueError("Duplicate OCI index")
                replaced = True
                member.size = len(index)
                target.addfile(member, io.BytesIO(index))
            else:
                target.addfile(
                    member, source.extractfile(member) if member.isfile() else None
                )
    if not replaced:
        raise ValueError("Archive has no OCI index")
    return found


def main() -> None:
    """Write a repaired tar stream and a public descriptor record for recovery."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kit", type=Path)
    parser.add_argument("descriptors", type=Path)
    args = parser.parse_args()
    found = repair(args.kit, sys.stdout.buffer)
    args.descriptors.write_text(json.dumps(found, indent=2) + "\n")


if __name__ == "__main__":
    main()
