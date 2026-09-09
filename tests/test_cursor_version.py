from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "modules/profile/files/cursor-latest-version"


@pytest.mark.parametrize(
    ("installed", "status"), [(None, 1), ("old", 1), ("latest", 0)]
)
def test_version_guard(tmp_path: Path, installed: str | None, status: int) -> None:
    commands = tmp_path / "commands"
    commands.mkdir()
    curl = commands / "curl"
    curl.write_text(
        "#!/bin/sh\nprintf '%s\\n' 'DOWNLOAD_URL=\"https://downloads.cursor.com/lab/latest/linux/x64/agent-cli-package.tar.gz\"'\n"
    )
    curl.chmod(0o755)
    home = tmp_path / "home"
    if installed:
        target = (
            home / ".local/share/cursor-agent/versions" / installed / "cursor-agent"
        )
        target.parent.mkdir(parents=True)
        target.touch()
        binary = home / ".local/bin/agent"
        binary.parent.mkdir(parents=True)
        binary.symlink_to(target)
    result = subprocess.run(
        ["/bin/sh", str(SCRIPT), "--current", str(home)],
        env=os.environ | {"PATH": f"{commands}:/usr/bin:/bin"},
        capture_output=True,
    )
    assert result.returncode == status
