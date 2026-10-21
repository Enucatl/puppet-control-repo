"""Exercise Neovim upgrades and temporary-file cleanup without downloads."""

import json
import os
from pathlib import Path
import subprocess
import tarfile

import pytest


ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize("version", ["v0.10.0", None])
@pytest.mark.parametrize("failure", [None, "download", "extract"])
def test_release_detection_and_cleanup(
    tmp_path: Path, version: str | None, failure: str | None
) -> None:
    """Upgrade changed releases, skip matching ones, and clean failed downloads."""
    install = tmp_path / "install"
    for directory in ("bin", "lib/nvim", "share/nvim"):
        (install / directory).mkdir(parents=True)
    marker = tmp_path / "version"
    marker.write_text("v0.9.0\n")
    values = tmp_path / "values.yaml"
    values.write_text(
        json.dumps(
            {
                "version": version,
                "asset_name": "nvim-linux-x86_64.tar.gz",
                "marker_file": str(marker),
                "install_dir": str(install),
                "install_bin": str(install / "bin"),
                "install_lib": str(install / "lib/nvim"),
                "install_share": str(install / "share/nvim"),
            }
        )
    )
    script = subprocess.run(
        [
            "/opt/puppetlabs/bin/puppet",
            "epp",
            "render",
            "--values_file",
            str(values),
            str(ROOT / "modules/neovim/templates/install-neovim.sh.epp"),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    source = tmp_path / "nvim-linux-x86_64"
    for name in ("bin/nvim", "lib/nvim/parser", "share/nvim/runtime"):
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("new release\n")
    archive = tmp_path / "release.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        output.add(source, arcname=source.name)
    if failure == "extract":
        archive.write_text("invalid archive")
    tools = tmp_path / "tools"
    tools.mkdir()
    curl = tools / "curl"
    curl.write_text("""#!/bin/bash
set -eu
if [[ $* == *api.github.com* ]]; then
    printf '{"tag_name":"%s","assets":[{"name":"nvim-linux-x86_64.tar.gz","browser_download_url":"https://example.invalid/nvim"}]}' "$RELEASE_TAG"
else
    echo download >> "$DOWNLOADS"
    [[ $FAILURE != download ]] || exit 22
    cp "$ARCHIVE" "${@: -1}"
fi
""")
    curl.chmod(0o755)
    chown = tools / "chown"
    chown.write_text("#!/bin/sh\nexit 0\n")
    chown.chmod(0o755)
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    downloads = tmp_path / "downloads"
    environment = dict(
        os.environ,
        PATH=f"{tools}:{os.environ['PATH']}",
        TMPDIR=str(temporary),
        ARCHIVE=str(archive),
        DOWNLOADS=str(downloads),
        RELEASE_TAG="v0.10.0",
        FAILURE=failure or "",
    )
    result = subprocess.run(
        ["bash", "-c", script],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert list(temporary.iterdir()) == []
    if failure:
        assert result.returncode != 0
        assert marker.read_text() == "v0.9.0\n"
        assert not (install / "bin/nvim").exists()
        return
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker.read_text() == "v0.10.0\n"
    assert (install / "bin/nvim").read_text() == "new release\n"
    subprocess.run(
        ["bash", "-c", script], env=environment, check=True, capture_output=True
    )
    assert downloads.read_text().splitlines() == ["download"]
    environment["RELEASE_TAG"] = "v0.11.0"
    subprocess.run(
        ["bash", "-c", script.replace("v0.10.0", "v0.11.0")],
        env=environment,
        check=True,
        capture_output=True,
    )
    assert marker.read_text() == "v0.11.0\n"
    (install / "bin/nvim").unlink()
    subprocess.run(
        ["bash", "-c", script.replace("v0.10.0", "v0.11.0")],
        env=environment,
        check=True,
        capture_output=True,
    )
    assert (install / "bin/nvim").exists()
    assert downloads.read_text().splitlines() == ["download"] * 3
    assert list(temporary.iterdir()) == []
