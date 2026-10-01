from pathlib import Path

import pytest

from puppet_catalog import compile_catalog, resources


@pytest.mark.parametrize(
    ("os_name", "release", "enabled"),
    [("Ubuntu", "24.04", True), ("Ubuntu", "22.04", False), ("Debian", "13.0", False)],
)
def test_bubblewrap_profile(
    tmp_path: Path, os_name: str, release: str, enabled: bool
) -> None:
    """Allow bubblewrap namespaces only on Ubuntu releases with AppArmor 4."""
    result = resources(
        compile_catalog(
            tmp_path,
            "include profile::bubblewrap",
            fact_overrides={"os": {"name": os_name, "release": {"full": release}}},
        )
    )
    assert ("File[/etc/apparmor.d/bwrap]" in result) == enabled
    if enabled:
        assert (
            result["File[/etc/apparmor.d/bwrap]"]["notify"]
            == "Exec[load-bwrap-apparmor]"
        )
        assert result["Exec[load-bwrap-apparmor]"]["refreshonly"] is True
        assert not any(name.startswith("Sysctl[") for name in result)
