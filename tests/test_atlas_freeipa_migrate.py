"""Keep production IPA imports isolated and protect migration credentials."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest


@pytest.fixture
def migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Load the controller without invoking Docker or reading authority state."""
    spec = importlib.util.spec_from_file_location(
        "atlas_freeipa_migrate",
        Path(__file__).resolve().parents[1] / "proxmox/atlas-freeipa-migrate.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.controller, "STATE", tmp_path)
    monkeypatch.setattr(module.infra, "run", Mock())
    return module


def metadata() -> dict:
    """Describe an isolated target with no public ports or authority volume."""
    return {
        "Config": {
            "Hostname": "ipa.home.arpa",
            "Labels": {"home.arpa.rehearsal": "ipa-migration"},
        },
        "HostConfig": {"PortBindings": {}},
        "NetworkSettings": {"Networks": {"atlas-ipa-rehearsal": {}}},
        "Mounts": [{"Name": "atlas_ipa_rehearsal"}],
    }


def test_live_import_is_refused(migration: ModuleType) -> None:
    """The authoritative target only accepts a dry-run."""
    migration.infra.run.return_value = json.dumps([metadata()])
    with pytest.raises(RuntimeError, match="isolated"):
        migration.target("rehearse", "infra-ipa-1")
    migration.target("dry-run", "infra-ipa-1")


@pytest.mark.parametrize("unsafe", ["ports", "network", "volume", "label"])
def test_rehearsal_guards(migration: ModuleType, unsafe: str) -> None:
    """Reject shared storage, routing, labels or listening ports."""
    target = metadata()
    if unsafe == "ports":
        target["HostConfig"]["PortBindings"] = {"443/tcp": [{"HostPort": "443"}]}
    elif unsafe == "network":
        target["NetworkSettings"]["Networks"]["infra"] = {}
    elif unsafe == "volume":
        target["Mounts"] = [{"Name": migration.controller.VOLUME}]
    else:
        target["Config"]["Labels"] = {}
    migration.infra.run.return_value = json.dumps([target])
    with pytest.raises(RuntimeError):
        migration.target("rehearse", "atlas-ipa-rehearsal")


def test_isolated_rehearsal_is_allowed(migration: ModuleType) -> None:
    """A dedicated clone satisfies all import boundaries."""
    migration.infra.run.return_value = json.dumps([metadata()])
    migration.target("rehearse", "atlas-ipa-rehearsal")


def test_credentials_must_be_private(migration: ModuleType, tmp_path: Path) -> None:
    """World-readable secret files are rejected before their content is parsed."""
    secret = tmp_path / "source.json"
    secret.write_text("not-json")
    secret.chmod(0o644)
    with pytest.raises(RuntimeError, match="mode 0600"):
        migration.private_json(secret)


def test_worker_requires_tls_and_retains_dryrun_evidence(migration: ModuleType) -> None:
    """Migration uses password files and authenticated source TLS, without DNS."""
    compile(migration.WORKER, "worker", "exec")
    assert "'--bind-pw-file'" in migration.WORKER
    assert "'--cacertfile'" in migration.WORKER
    assert "'--skip-config'" in migration.WORKER
    assert "'--dryrun-record'" in migration.WORKER
    assert "conn.start_tls_s()" in migration.WORKER
    assert "target_addresses <= local_addresses" in migration.WORKER
    assert "['kinit', 'admin@HOME.ARPA']" in migration.WORKER
    assert "input=request['target_admin_password']" in migration.WORKER
    assert "env=environment" in migration.WORKER
    assert "default_ccache_name = FILE:" in migration.WORKER
    assert "forwardable = true" in migration.WORKER
    assert "--migrate-dns" not in migration.WORKER
    assert "before['trust'] == after['trust']" in migration.WORKER
    assert "before['schema'] == after['schema']" in migration.WORKER
    assert "['kinit', 'puppet-enroller@HOME.ARPA']" in migration.WORKER
    assert "assert not keys_before" in migration.WORKER
    assert "assert keys_after" in migration.WORKER


@pytest.mark.parametrize(
    "action, report",
    [
        ("dry-run", {"returncode": 1, "stderr": "private"}),
        (
            "rehearse",
            {
                "returncode": 0,
                "trust_unchanged": True,
                "login_error": {"type": "CalledProcessError"},
            },
        ),
    ],
)
def test_failure_keeps_private_report(
    migration: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    report: dict,
) -> None:
    """A failed command retains evidence before returning an error."""
    source = tmp_path / "source.json"
    source.write_text('{"dm_password":"source-secret"}')
    source.chmod(0o600)
    (tmp_path / "installer.json").write_text(
        '{"dm_password":"target-secret","admin_password":"admin-secret"}'
    )
    (tmp_path / "root.pem").write_text("new-ca")
    source_ca = tmp_path / "old-ca.pem"
    source_ca.write_text("old-ca")
    monkeypatch.setattr(migration, "private_json", lambda p: json.loads(p.read_text()))
    monkeypatch.setattr(migration, "target", Mock())
    migration.infra.run.return_value = json.dumps(report)
    login = tmp_path / "login.json"
    login.write_text('{"password":"private"}')
    with pytest.raises(RuntimeError, match="private evidence"):
        migration.migrate(action, "target", source, source_ca, login)
    evidence = list(tmp_path.glob("migration-*.json"))
    assert len(evidence) == 1
    assert evidence[0].stat().st_mode & 0o777 == 0o600
    command, payload = migration.infra.run.call_args.args
    assert "source-secret" not in repr(command)
    assert json.loads(payload)["source_password"] == "source-secret"


def test_rehearsal_requires_password_login_proof(
    migration: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rehearsal cannot skip proving the migrated account password works."""
    monkeypatch.setattr(migration, "private_json", lambda _: {"dm_password": "private"})
    with pytest.raises(RuntimeError, match="login-password-file"):
        migration.migrate("rehearse", "atlas-ipa-rehearsal", Path("source"), Path("ca"))
    migration.infra.run.assert_not_called()


def test_database_export_filters_authorities_only(
    migration: ModuleType, tmp_path: Path
) -> None:
    """Keep accounts and similarly named entries while dropping old CA subtrees."""
    import ast
    from types import SimpleNamespace

    class DN(str):
        """Provide DN suffix containment for the exporter unit test."""

        def __new__(cls, *parts: str) -> DN:
            return super().__new__(cls, ",".join(parts).lower())

        def __contains__(self, ancestor: object) -> bool:
            return self == ancestor or self.endswith("," + str(ancestor))

    suffix = "dc=home,dc=arpa"
    retained = [
        suffix,
        "uid=alice,cn=users,cn=accounts," + suffix,
        "cn=ca-other," + suffix,
        "cn=alice,cn=groups,cn=accounts," + suffix,
    ]
    omitted = [
        "cn=ca," + suffix,
        "cn=profile,cn=ca," + suffix,
        "cn=certificates,cn=ipa,cn=etc," + suffix,
        "cn=old-root,cn=certificates,cn=ipa,cn=etc," + suffix,
    ]
    conn = Mock()
    conn.search_s.return_value = [(dn, {"cn": [b"entry"]}) for dn in omitted + retained]
    conn.search_s.return_value[-1][1]["mepManagedBy"] = [b"uid=alice"]
    writer = Mock()
    namespace = {
        "DN": DN,
        "ldap": SimpleNamespace(SCOPE_SUBTREE=2),
        "ldif": SimpleNamespace(LDIFWriter=lambda _: writer),
    }
    function = next(
        node
        for node in ast.parse(migration.WORKER).body
        if isinstance(node, ast.FunctionDef) and node.name == "export_database"
    )
    exec(
        compile(ast.Module(body=[function], type_ignores=[]), "export", "exec"),
        namespace,
    )
    counts = namespace["export_database"](conn, tmp_path / "source.ldif")
    exported = [call.args[0] for call in writer.unparse.call_args_list]
    assert exported[0] == suffix
    assert exported[-1] == "cn=alice,cn=groups,cn=accounts," + suffix
    assert set(exported) == set(retained)
    assert counts == {"exported": len(retained), "excluded_authority": len(omitted)}


def test_hbac_comparison_accepts_only_unique_same_parent_name(
    migration: ModuleType,
) -> None:
    """Accept native HBAC UUID reuse while rejecting policy or identity changes."""
    import ast

    function = next(
        node
        for node in ast.parse(migration.WORKER).body
        if isinstance(node, ast.FunctionDef) and node.name == "compare_identities"
    )
    namespace = {"DN": lambda value: tuple(value.lower().split(","))}
    exec(
        compile(ast.Module(body=[function], type_ignores=[]), "compare", "exec"),
        namespace,
    )
    compare = namespace["compare_identities"]
    attrs = {"cn": ["rule"], "ipaenabledflag": ["TRUE"]}
    source = {
        "users": {},
        "groups": {},
        "sudo": {},
        "hbac": {"ipauniqueid=old,cn=hbac,dc=home,dc=arpa": attrs},
    }
    target = {
        **source,
        "hbac": {"ipauniqueid=new,cn=hbac,dc=home,dc=arpa": dict(attrs)},
    }
    assert compare(source, target)["hbac"] == 0
    target["hbac"]["ipauniqueid=new,cn=hbac,dc=home,dc=arpa"]["ipaenabledflag"] = [
        "FALSE"
    ]
    assert compare(source, target)["hbac"] == 1
    target["hbac"] = {"cn=rule,cn=hbacservices,cn=hbac,dc=home,dc=arpa": attrs}
    assert compare(source, target)["hbac"] == 1
    target["hbac"] = {
        "ipauniqueid=first,cn=hbac,dc=home,dc=arpa": attrs,
        "ipauniqueid=second,cn=hbac,dc=home,dc=arpa": attrs,
    }
    assert compare(source, target)["hbac"] == 1
