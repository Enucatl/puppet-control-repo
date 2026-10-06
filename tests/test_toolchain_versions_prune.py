from pathlib import Path
import subprocess


SCRIPT = (
    Path(__file__).resolve().parents[1] / "modules/profile/files/toolchain-versions-prune"
)


def test_keeps_two_newest_versions(tmp_path: Path) -> None:
    releases = tmp_path / "releases"
    releases.mkdir()
    for version in (
        "0.157.0-x86_64-unknown-linux-musl",
        "0.159.2-x86_64-unknown-linux-musl",
        "0.160.0-x86_64-unknown-linux-musl",
        "0.160.1-x86_64-unknown-linux-musl",
    ):
        (releases / version).mkdir()

    subprocess.run(["bash", str(SCRIPT), str(releases)], check=True)

    assert sorted(path.name for path in releases.iterdir()) == [
        "0.160.0-x86_64-unknown-linux-musl",
        "0.160.1-x86_64-unknown-linux-musl",
    ]
