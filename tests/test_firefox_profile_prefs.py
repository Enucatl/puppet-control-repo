"""Check per-profile Firefox preferences preserve user-owned configuration."""

import importlib.util
from pathlib import Path
import stat

import pytest

from puppet_catalog import ROOT


SPEC = importlib.util.spec_from_file_location(
    "firefox_profile_prefs",
    ROOT / "modules/freeipa_firefox/files/firefox-profile-prefs.py",
)
assert SPEC and SPEC.loader
PREFS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREFS)


def test_existing_profiles_preserve_settings_modes_and_detect_drift(
    tmp_path: Path,
) -> None:
    """Update all existing profiles without touching unrelated files or check mode."""
    profiles = [
        tmp_path / ".mozilla/firefox/first",
        tmp_path / "snap/firefox/common/.mozilla/firefox/second",
        tmp_path / ".config/mozilla/firefox/third",
    ]
    for profile in profiles:
        profile.mkdir(parents=True)
        (profile / "prefs.js").write_text("// Firefox-owned preferences\n")
    target = profiles[0] / "user.js"
    original = 'user_pref("browser.startup.page", 3);\r\n'
    target.write_bytes(original.encode())
    target.chmod(0o640)
    template = tmp_path / "template.js"
    template.write_text('user_pref("device.sensors.enabled", false);\n')

    assert not PREFS.synchronize(tmp_path, template, check=True)
    assert target.read_bytes() == original.encode()
    assert not (profiles[1] / "user.js").exists()
    assert not PREFS.synchronize(tmp_path, template)
    for profile in profiles:
        content = (profile / "user.js").read_text()
        assert template.read_text() in content
        assert content.count(PREFS.BEGIN) == 1
        assert (profile / "prefs.js").read_text() == "// Firefox-owned preferences\n"
    assert target.read_bytes().startswith(original.encode())
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert stat.S_IMODE((profiles[1] / "user.js").stat().st_mode) == 0o600
    before = target.stat().st_mtime_ns
    assert PREFS.synchronize(tmp_path, template)
    assert target.stat().st_mtime_ns == before

    suffix = "// Keep this too\n"
    target.write_bytes(target.read_bytes() + suffix.encode())
    template.write_text('user_pref("device.sensors.enabled", true);\n')
    assert not PREFS.synchronize(tmp_path, template, check=True)
    assert not PREFS.synchronize(tmp_path, template)
    assert target.read_bytes().startswith(original.encode())
    assert target.read_bytes().endswith(suffix.encode())
    assert template.read_text() in target.read_text()
    assert PREFS.synchronize(tmp_path, template, check=True)


@pytest.mark.parametrize(
    "content",
    [PREFS.BEGIN, PREFS.END, PREFS.END + PREFS.BEGIN, PREFS.BEGIN * 2 + PREFS.END],
)
def test_malformed_markers_preserve_user_js(tmp_path: Path, content: str) -> None:
    """Refuse ambiguous managed blocks without replacing user configuration."""
    profile = tmp_path / ".mozilla/firefox/existing"
    profile.mkdir(parents=True)
    (profile / "prefs.js").touch()
    target = profile / "user.js"
    target.write_text(content)
    template = tmp_path / "template.js"
    template.write_text('user_pref("device.sensors.enabled", false);\n')
    for check in (True, False):
        with pytest.raises(ValueError, match="Malformed Puppet preference block"):
            PREFS.synchronize(tmp_path, template, check=check)
        assert target.read_text() == content


def test_no_existing_profiles_creates_nothing(tmp_path: Path) -> None:
    """Leave homes without Firefox profiles untouched."""
    template = tmp_path / "template.js"
    template.write_text('user_pref("device.sensors.enabled", false);\n')
    assert PREFS.synchronize(tmp_path, template)
    assert list(tmp_path.iterdir()) == [template]
