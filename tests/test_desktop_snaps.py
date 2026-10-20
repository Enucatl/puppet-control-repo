"""Check native Snap resources independently of global APT package defaults."""

from pathlib import Path

from puppet_catalog import compile_catalog, resources


def test_desktop_snaps_use_native_provider_and_stable_channels(tmp_path: Path) -> None:
    """Order strict stable installs after snapd and connect the Moonlight gamepad."""
    catalog = compile_catalog(
        tmp_path,
        "Package { provider => apt }\ninclude profile::desktop_snaps",
        {},
        hostname="aperture",
        node_type="desktop",
    )
    result = resources(catalog)
    assert result["Package[snapd]"]["provider"] == "apt"
    assert result["Service[snapd]"]["ensure"] == "running"
    assert result["Service[snapd]"]["enable"] is True
    for name in ("telegram-desktop", "moonlight"):
        package = result[f"Package[{name}]"]
        assert package["provider"] == "snap"
        assert package["ensure"] == "installed"
        assert package["install_options"] == ["--channel=latest/stable"]
        assert package["require"] == "Service[snapd]"
    joystick = result["Exec[moonlight-joystick]"]
    assert joystick["command"] == "/usr/bin/snap connect moonlight:joystick :joystick"
    assert "moonlight:joystick" in joystick["unless"]
    assert joystick["require"] == "Package[moonlight]"
