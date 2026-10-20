"""Check managed archive pins, launchers and permissions without installing apps."""

from pathlib import Path

import pytest

from puppet_catalog import compile_catalog, resources


PINS = {
    "profile::desktop_archives::reaper_version": "7.82",
    "profile::desktop_archives::reaper_source": "https://www.reaper.fm/files/7.x/reaper782_linux_x86_64.tar.xz",
    "profile::desktop_archives::reaper_checksum": "a4aa961534f22ae5ae910e4ec0c200dcb4476c9f7556806fe09a9743cd582563",
    "profile::desktop_archives::teamspeak_version": "6.0.0-beta5",
    "profile::desktop_archives::teamspeak_source": "https://files.teamspeak-services.com/pre_releases/client/6.0.0-beta5/teamspeak-client.tar.xz",
    "profile::desktop_archives::teamspeak_checksum": "394472a25ee9c2419c44a5e71a401c21bffecf2fdcb27fbe2173d0fffd1875a4",
}


def test_archive_catalog_preserves_home_and_enforces_pins(tmp_path: Path) -> None:
    """Install verified archives into root-owned version directories with launchers."""
    result = resources(
        compile_catalog(
            tmp_path, "include profile::desktop_archives", PINS, hostname="aperture"
        )
    )
    for app, version, executable in (
        ("reaper", "7.82", "REAPER/reaper"),
        ("teamspeak", "6.0.0-beta5", "TeamSpeak"),
    ):
        folder = f"/opt/{app}-{version}"
        directory = result[f"File[{folder}]"]
        assert directory["owner"] == directory["group"] == "root"
        assert directory["mode"] == "0755"
        archive = result[f"Archive[/var/cache/{app}-{version}.tar.xz]"]
        assert archive["source"] == PINS[f"profile::desktop_archives::{app}_source"]
        assert archive["checksum_type"] == "sha256"
        assert archive["cleanup"] is True
        assert archive["checksum"] == PINS[f"profile::desktop_archives::{app}_checksum"]
        assert archive["creates"] == f"{folder}/{executable}"
        assert "--no-same-owner" in archive["extract_command"]
        assert ("--strip-components=1" in archive["extract_command"]) == (
            app == "reaper"
        )
        launcher = result[f"File[/usr/local/bin/{app}]"]
        assert launcher["ensure"] == "link"
        assert launcher["target"] == f"{folder}/{executable}"
    sandbox = result["File[/opt/teamspeak-6.0.0-beta5/chrome-sandbox]"]
    assert sandbox["owner"] == sandbox["group"] == "root"
    assert sandbox["mode"] == "4755"
    assert not any(name.startswith("File[/home/") for name in result)
    assert "Package[libgtk-3-0t64]" in result
    assert "Package[libnss3]" in result
    for entry in ("cockos-reaper", "teamspeak-client"):
        desktop = result[f"File[/usr/local/share/applications/{entry}.desktop]"]
        assert "Exec=/usr/local/bin/" in desktop["content"]
        assert "--no-sandbox" not in desktop["content"]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("reaper_checksum", "invalid-checksum"),
        ("teamspeak_source", "http://example.org/unverified.tar.xz"),
        ("reaper_version", "7.82;touch /tmp/injected"),
    ],
)
def test_archive_catalog_rejects_unsafe_pins(
    tmp_path: Path, key: str, value: str
) -> None:
    """Reject unsupported source schemes, unchecked downloads and shell syntax."""
    with pytest.raises(AssertionError, match="expects a match"):
        compile_catalog(
            tmp_path,
            "include profile::desktop_archives",
            {**PINS, f"profile::desktop_archives::{key}": value},
            hostname="aperture",
        )
