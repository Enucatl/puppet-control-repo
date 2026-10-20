"""Check recovery reuses Aperture's identity and completes desktop setup."""

import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("uid", "hostname"),
    [
        ("1000", "aperture.home.arpa"),
        ("0", "forbearance.home.arpa"),
        ("0", "aperture.home.arpa"),
    ],
)
def test_finalize_existing_desktop(tmp_path: Path, uid: str, hostname: str) -> None:
    """Refuse unrelated guests and resume without using an enrollment token."""
    commands = tmp_path / "bin"
    commands.mkdir()
    for name, output in (("id", uid), ("hostname", hostname)):
        path = commands / name
        path.write_text(f"#!/bin/sh\nprintf '%s\\n' '{output}'\n")
        path.chmod(0o755)
    for name in ("configure-puppet", "systemctl"):
        path = commands / name
        path.write_text(f'#!/bin/sh\nprintf "%s\\n" "{name} $*" >> "$CALLS"\n')
        path.chmod(0o755)
    script = tmp_path / "finalize.sh"
    script.write_text(
        (ROOT / "proxmox/aperture-finalize.sh")
        .read_text()
        .replace(
            "/usr/local/bin/configure-puppet.sh", str(commands / "configure-puppet")
        )
    )
    calls = tmp_path / "calls"
    result = subprocess.run(
        ["bash", str(script)],
        env={**os.environ, "PATH": f"{commands}:/usr/bin:/bin", "CALLS": str(calls)},
        capture_output=True,
        text=True,
        timeout=5,
    )
    if uid != "0" or hostname != "aperture.home.arpa":
        assert result.returncode == 1
        assert not calls.exists()
        return
    assert result.returncode == 0, result.stderr
    assert calls.read_text().splitlines() == [
        "configure-puppet --apply aperture.home.arpa openvox.home.arpa",
        "systemctl start qemu-guest-agent",
        "systemctl set-default graphical.target",
        "systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target",
    ]
