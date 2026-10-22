from pathlib import Path

import pytest

from puppet_catalog import compile_catalog, resources


@pytest.mark.parametrize(
    ("architecture", "target", "checksum"),
    [
        (
            "amd64",
            "x86_64-unknown-linux-musl",
            "a6d077d0a95eed5a2eba715b2dae06be954f624ccbf85758a03f389ded33118c",
        ),
        (
            "arm64",
            "aarch64-unknown-linux-musl",
            "5aa8d74a3d906e60ea12a66ac2f37f8eef1b14cbad7182a745e393a10c23dcee",
        ),
    ],
)
def test_typst_bootstrap_and_weekly_update(
    tmp_path: Path, architecture: str, target: str, checksum: str
) -> None:
    """Bootstrap the correct binary without putting update work in Puppet runs."""
    catalog = compile_catalog(
        tmp_path,
        "include profile::typst, profile::common::cron",
        hostname="aperture",
        node_type="desktop",
        fact_overrides={
            "os": {"architecture": architecture, "family": "Debian", "name": "Ubuntu"}
        },
    )
    result = resources(catalog)
    archive = result["Archive[/var/cache/typst.tar.xz]"]
    assert archive["source"].endswith(f"/v0.15.1/typst-{target}.tar.xz")
    assert archive["checksum_type"] == "sha256"
    assert archive["checksum"] == checksum
    assert archive["creates"] == "/usr/local/bin/typst"
    assert archive["cleanup"] is True
    assert archive["extract_command"].endswith(f"typst-{target}/typst")
    binary = result["File[/usr/local/bin/typst]"]
    assert binary["owner"] == binary["group"] == "root"
    assert binary["mode"] == "0755"
    assert "content" not in binary and "source" not in binary
    update = result["Cron[typst-update]"]
    assert update["user"] == "root"
    assert update["minute"] == 15
    assert update["hour"] == 3
    assert update["weekday"] == 0
    assert (
        "typst update --backup-path /var/lib/typst/typst_backup.part"
        in update["command"]
    )
    assert "Cron[user-toolchain-cache-prune]" in result
    assert not any(name.startswith("Exec[") for name in result)
