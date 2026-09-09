from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "migrate_profile_secrets",
    Path(__file__).parents[1] / "scripts/migrate_profile_secrets.py",
)
assert SPEC is not None and SPEC.loader is not None
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)


def test_copy_preserves_existing_canonical_fields() -> None:
    legacy = {old: f"fixture-{index}" for index, old in enumerate(migration.KEYS)}
    data = legacy | {
        "unrelated": "unchanged",
        "profile::alloy::maxmind_account_id": "override",
    }
    original = data.copy()
    patch = migration.migration_patch(data)
    assert data == original
    assert len(patch) == 3
    assert "unrelated" not in patch
    assert "profile::alloy::maxmind_account_id" not in patch
    assert migration.migration_patch(data | patch) == {}


def test_missing_source_is_an_error_without_exposing_values() -> None:
    with pytest.raises(ValueError, match="Missing both"):
        migration.migration_patch({})


def test_apply_uses_cas_and_stdin(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    data = {old: f"secret-{index}" for index, old in enumerate(migration.KEYS)}
    calls = []

    def fake_vault(*args: str, payload: dict | None = None) -> dict:
        calls.append((args, payload))
        if payload:
            data.update(payload)
        return {"data": {"data": data.copy(), "metadata": {"version": 7}}}

    monkeypatch.setattr(migration, "vault", fake_vault)
    monkeypatch.setattr(
        migration, "check_deployed_configuration", lambda environment: None
    )
    monkeypatch.setattr("sys.argv", ["migrate_profile_secrets.py", "--apply"])
    migration.main()
    assert calls[1][0] == ("patch", "-format=json", "-cas=7", "kv/puppet", "-")
    assert len(calls[1][1]) == 4
    assert len(data) == 8
    assert "secret-" not in capsys.readouterr().out


def test_apply_refuses_an_unprepared_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "migrate_profile_secrets.py",
            "--apply",
            "--deployed-environment",
            str(tmp_path),
        ],
    )
    with pytest.raises(RuntimeError, match="Deployed Hiera data not found"):
        migration.main()


@pytest.mark.parametrize("converted", [False, True])
def test_deployed_samba_conversion(tmp_path: Path, converted: bool) -> None:
    data = tmp_path / "data"
    data.mkdir()
    options = {
        key: {"convert_to": "Sensitive"}
        for key in migration.KEYS.values()
        if converted and key.startswith("profile::docker_node::")
    }
    (data / "common.yaml").write_text(migration.json.dumps({"lookup_options": options}))
    if converted:
        migration.check_deployed_configuration(tmp_path)
    else:
        with pytest.raises(RuntimeError, match="Deploy the Sensitive conversion"):
            migration.check_deployed_configuration(tmp_path)
