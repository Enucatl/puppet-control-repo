"""Check the container startup guard using disposable IPA volume paths."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shlex
import stat
from types import ModuleType
from unittest.mock import Mock

import pytest


@pytest.fixture
def entrypoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Map only container-owned paths into an isolated directory."""
    spec = importlib.util.spec_from_file_location(
        "atlas_ipa_entrypoint",
        Path(__file__).resolve().parents[1] / "docker/freeipa/atlas-entrypoint.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / "data").mkdir()
    (tmp_path / "bootstrap").mkdir()
    settings = {
        "hostname": "ipa.home.arpa",
        "domain": "home.arpa",
        "realm": "HOME.ARPA",
        "no_pkinit": True,
        "volume_identity": "original-volume",
        "admin_password": "spaces and '$()' preserve this",
        "dm_password": "manager password",
    }
    (tmp_path / "bootstrap/installer.json").write_text(json.dumps(settings))
    (tmp_path / "data/.atlas-freeipa-volume").write_text("original-volume\n")
    monkeypatch.setattr(module, "Path", lambda path: tmp_path / path.lstrip("/"))
    monkeypatch.setattr(module.socket, "gethostname", lambda: "ipa.home.arpa")
    monkeypatch.setattr(module.os, "umask", Mock())
    monkeypatch.setattr(module.os, "execv", Mock())
    return module


def test_fresh_prepared_volume_writes_protected_canonical_options(
    entrypoint: ModuleType,
) -> None:
    """Passwords survive shell quoting and never enter the exec argument list."""
    entrypoint.main()
    path = entrypoint.Path("/data/ipa-server-install-options")
    options = [shlex.split(line)[0] for line in path.read_text().splitlines()]
    settings = json.loads(entrypoint.Path("/bootstrap/installer.json").read_text())
    assert options == entrypoint.installer_options(settings)
    assert f"--admin-password={settings['admin_password']}" in options
    assert "--no-pkinit" in options
    assert "--external-ca" not in options
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    entrypoint.os.execv.assert_called_once_with(
        "/usr/local/sbin/init", ["/usr/local/sbin/init"]
    )


def test_established_volume_reuses_installer_state(entrypoint: ModuleType) -> None:
    """A healthy previous installation starts without regenerating options."""
    entrypoint.Path("/data/.atlas-freeipa-installed").write_text("original-volume\n")
    config = entrypoint.Path("/data/etc/ipa/default.conf")
    config.parent.mkdir(parents=True)
    config.write_text("existing IPA configuration")
    options = entrypoint.Path("/data/ipa-server-install-options")
    options.write_text("saved original options")
    entrypoint.main()
    assert options.read_text() == "saved original options"
    entrypoint.os.execv.assert_called_once()


@pytest.mark.parametrize(
    ("volume", "installed", "config", "message"),
    [
        (None, None, False, "Missing prepared"),
        ("other-volume", None, False, "does not match"),
        ("original-volume", "other-volume", True, "Established FreeIPA state missing"),
        (
            "original-volume",
            "original-volume",
            False,
            "Established FreeIPA state missing",
        ),
    ],
)
def test_unrecognized_or_lost_state_never_executes_init(
    entrypoint: ModuleType,
    volume: str | None,
    installed: str | None,
    config: bool,
    message: str,
) -> None:
    """Lost installation data cannot silently become a new authority."""
    marker = entrypoint.Path("/data/.atlas-freeipa-volume")
    if volume is None:
        marker.unlink()
    else:
        marker.write_text(volume)
    if installed:
        entrypoint.Path("/data/.atlas-freeipa-installed").write_text(installed)
    if config:
        path = entrypoint.Path("/data/etc/ipa/default.conf")
        path.parent.mkdir(parents=True)
        path.write_text("configured")
    with pytest.raises(RuntimeError, match=message):
        entrypoint.main()
    entrypoint.os.execv.assert_not_called()
    assert not entrypoint.Path("/data/ipa-server-install-options").exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("hostname", "freeipa.home.arpa"),
        ("domain", "example.test"),
        ("realm", "OTHER.REALM"),
        ("no_pkinit", False),
        ("admin_password", ""),
        ("dm_password", None),
    ],
)
def test_changed_install_contract_fails_before_init(
    entrypoint: ModuleType, field: str, value: object
) -> None:
    """Only the agreed CA-less identity and supplied passwords may initialize."""
    path = entrypoint.Path("/bootstrap/installer.json")
    settings = json.loads(path.read_text())
    settings[field] = value
    path.write_text(json.dumps(settings))
    with pytest.raises(ValueError):
        entrypoint.main()
    entrypoint.os.execv.assert_not_called()
    assert not entrypoint.Path("/data/ipa-server-install-options").exists()
