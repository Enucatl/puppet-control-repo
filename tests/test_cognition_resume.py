"""Check the recovery entry point refuses unrelated guests before mutation."""

import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("uid", "hostname"), [("1000", "cognition.home.arpa"), ("0", "complex.home.arpa")]
)
def test_resume_checks_identity(tmp_path: Path, uid: str, hostname: str) -> None:
    """Reject nonroot callers and other nodes before touching guest state."""
    commands = tmp_path / "bin"
    commands.mkdir()
    for name, output in (("id", uid), ("hostname", hostname)):
        path = commands / name
        path.write_text(f"#!/bin/sh\nprintf '%s\\n' '{output}'\n")
        path.chmod(0o755)
    result = subprocess.run(
        ["/bin/bash", str(ROOT / "proxmox/cognition-resume.sh")],
        env={**os.environ, "PATH": f"{commands}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 1
    assert result.stderr.strip() == "Run as root on cognition.home.arpa."
