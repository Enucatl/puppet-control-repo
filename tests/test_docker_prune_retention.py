"""Exercise the image-retention guard without touching the host Docker daemon."""

import os
import shlex
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "image,anchor,label,success,creates",
    [
        ("", "", "", True, False),
        ("sha256:target", "", "", True, True),
        ("sha256:target", "sha256:target", "true", True, False),
        ("sha256:target", "sha256:other", "true", False, False),
        ("sha256:target", "sha256:target", "", False, False),
        ("", "sha256:other", "true", False, False),
    ],
)
def test_retention_guard(
    tmp_path: Path, image: str, anchor: str, label: str, success: bool, creates: bool
) -> None:
    """Create only the intended local image anchor and reject name collisions."""
    data = yaml.safe_load((ROOT / "data/nodes/cerebrum.home.arpa.yaml").read_text())
    unit = data["systemd::dropin_files"]["retain-paddle.conf"]["content"]
    unit = unit.replace("\\\n", " ")
    command = next(
        line for line in unit.splitlines() if line.startswith("ExecStartPre=")
    )
    args = shlex.split(command.removeprefix("ExecStartPre=").replace("$$", "$"))
    fake = tmp_path / "docker"
    fake.write_text(
        """#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$CALLS"
case "$1 $2" in
  'image inspect')
    test -n "$IMAGE" || exit 1
    if [ "$#" -gt 3 ]; then printf '%s\\n' "$IMAGE"; fi ;;
  'container inspect')
    test -n "$ANCHOR" || exit 1
    if [ "$#" -gt 3 ]; then
      case "$4" in
        '{{.Image}}') printf '%s\\n' "$ANCHOR" ;;
        *) printf '%s\\n' "$LABEL" ;;
      esac
    fi ;;
  'create --pull=never') exit 0 ;;
  *) exit 99 ;;
esac
"""
    )
    fake.chmod(0o755)
    args[-1] = args[-1].replace("/usr/bin/docker", str(fake))
    calls = tmp_path / "calls"
    result = subprocess.run(
        args,
        env={
            **os.environ,
            "IMAGE": image,
            "ANCHOR": anchor,
            "LABEL": label,
            "CALLS": str(calls),
            "RETAIN_IMAGE": "registry/paddle@sha256:target",
            "RETAIN_CONTAINER": "puppet-retain-paddle-recognition",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) is success, result.stderr
    commands = calls.read_text().splitlines()
    creation = [command for command in commands if command.startswith("create ")]
    assert bool(creation) is creates
    if creates:
        assert creation == [
            "create --pull=never --name puppet-retain-paddle-recognition "
            "--label puppet_keep_paddle_recognition=true --network none "
            "--userns host --entrypoint /bin/true registry/paddle@sha256:target"
        ]
    assert not any(
        command.startswith(("pull ", "start ", "rm ")) for command in commands
    )
