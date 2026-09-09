from __future__ import annotations

from pathlib import Path
import os
import re
from typing import Any

import pytest

from puppet_catalog import compile_catalog, resources

DUMMY_SECRETS = {
    "freeipa::client::password": "fixture-password",
    "freeipa_users::admin_password": "fixture-password",
    "profile::beszel_agent::key": "fixture-key",
    "profile::beszel_agent::token": "fixture-token",
    "profile::alloy::maxmind_account_id": "12345",
    "profile::alloy::maxmind_license_key": "fixture-license",
    "profile::docker_node::printer_smb_password": "fixture-printer",
    "profile::docker_node::pictures_smb_password": "fixture-pictures",
    "smtp_sasl_username": "fixture@example.org",
    "smtp_sasl_password": "fixture-smtp",
    "recipient_canonical": "fixture@example.org",
    "ipv6-prefix": "2001:db8:1234",
}


@pytest.mark.parametrize(
    ("hostname", "node_type"),
    [
        ("docker", "docker"),
        ("complex", "docker"),
        ("forbearance", "desktop"),
        ("proxmox", "proxmox"),
        ("proxmox-cortex", "proxmox"),
    ],
)
def test_node_catalog(tmp_path: Path, hostname: str, node_type: str) -> None:
    catalog = compile_catalog(
        tmp_path,
        "lookup('classes', Array[String]).include",
        DUMMY_SECRETS,
        hostname=hostname,
        node_type=node_type,
    )
    assert "Service[alloy]" in resources(catalog)
    result = resources(catalog)
    docker_role = node_type == "docker"
    assert ("User[alloy]" in result) == docker_role
    assert ("Package[geoipupdate]" in result) == docker_role
    assert ("Exec[allow-wolf-wol-directed-broadcast]" in result) == docker_role
    config = result["File[/etc/alloy/config.alloy]"]["content"]
    assert ('discovery.docker "containers"' in config) == docker_role
    assert ('loki.source.api "suricata_push"' in config) == (hostname == "docker")
    if hostname == "docker":
        assert config.count("stage.geoip {") == 4
        assert "__LOCAL_IPV6_PREFIX__" not in config
        assert "2001:db8:1234:" in config
    if hostname == "proxmox":
        assert "File[/usr/local/lib/proxmox_orchestration.py]" in result
        assert (
            result["File[/usr/local/sbin/chronicle-backup-orchestrator]"]["source"]
            == "puppet:///modules/proxmox_workflows/chronicle_backup_orchestrator.py"
        )

    baseline = os.environ.get("PROFILE_BASELINE_ROOT")
    if baseline:
        before = compile_catalog(
            tmp_path / "before",
            "lookup('classes', Array[String]).include",
            DUMMY_SECRETS,
            hostname=hostname,
            node_type=node_type,
            root=Path(baseline),
        )
        assert managed_resources(catalog) == managed_resources(before)
        assert ordered_pairs(catalog) == ordered_pairs(before)
        assert notifications(catalog) == notifications(before)


def managed_resources(catalog: dict[str, Any]) -> dict[str, Any]:
    """Compare native resources, allowing only source relocation and Alloy formatting."""
    result = {}
    for ref, params in resources(catalog).items():
        kind = ref.split("[")[0]
        if kind in ("Class", "Stage") or "::" in kind:
            continue
        params = {
            key: value
            for key, value in params.items()
            if key not in ("require", "before", "notify", "subscribe")
        }
        source = params.get("source")
        if isinstance(source, str) and source.startswith(
            "puppet:///modules/proxmox_workflows/"
        ):
            params["source"] = source.replace("/proxmox_workflows/", "/profile/")
        if ref == "File[/etc/alloy/config.alloy]":
            params["content"] = re.findall(
                r'"(?:\\.|[^"\\])*"|[A-Za-z_][A-Za-z_0-9.]*|[^\s]', params["content"]
            )
        result[ref] = params
    return result


def ordered_pairs(catalog: dict[str, Any]) -> set[tuple[str, str]]:
    """Compare effective ordering through Puppet's class/container anchors."""
    native = managed_resources(catalog).keys()
    edges: dict[str, set[str]] = {}
    for edge in catalog["relationships"]:
        edges.setdefault(edge["source"], set()).add(edge["target"])
    result = set()
    for source in native:
        todo = list(edges.get(source, ()))
        seen = set()
        while todo:
            target = todo.pop()
            if target in seen:
                continue
            seen.add(target)
            todo.extend(edges.get(target, ()))
            if target in native:
                result.add((source, target))
    return result


def notifications(catalog: dict[str, Any]) -> set[tuple[str, str, str, str]]:
    native = managed_resources(catalog).keys()
    edges: dict[str, list[dict[str, Any]]] = {}
    for edge in catalog["relationships"]:
        if edge.get("event") and edge["event"] != "NONE":
            edges.setdefault(edge["source"], []).append(edge)
    result = set()
    for source in native:
        todo = list(edges.get(source, ()))
        seen = set()
        while todo:
            edge = todo.pop()
            target = edge["target"]
            if target in seen:
                continue
            seen.add(target)
            if target in native:
                result.add((source, target, edge["event"], edge.get("callback", "")))
            else:
                todo.extend(edges.get(target, ()))
    return result


def test_docker_deploy_catalog(tmp_path: Path) -> None:
    catalog = compile_catalog(
        tmp_path,
        "include profile::alloy\nprofile::docker_deploy { 'example': scheduled_refresh => true, watch_dir => 'docker' }",
        hostname="fixture",
        node_type="proxmox",
    )
    result = resources(catalog)
    deploy = result["File[/etc/systemd/system/example-deploy.service]"]["content"]
    refresh = result["File[/etc/systemd/system/example-refresh.service]"]["content"]
    assert "ExecCondition=" in deploy
    assert "ExecCondition=" not in refresh
    assert "docker-compose-health-check" in deploy
    assert "docker-compose-health-check" in refresh


def test_base_alloy_catalog(tmp_path: Path) -> None:
    catalog = compile_catalog(
        tmp_path, "include profile::alloy", hostname="proxmox", node_type="proxmox"
    )
    result = resources(catalog)
    assert "Service[alloy]" in result


@pytest.mark.parametrize("mode", ["complete", "missing", "partial"])
def test_canonical_secret_consumers_and_geoip_gate(tmp_path: Path, mode: str) -> None:
    data = DUMMY_SECRETS.copy()
    if mode == "missing":
        del data["profile::alloy::maxmind_account_id"]
        del data["profile::alloy::maxmind_license_key"]
    elif mode == "partial":
        del data["profile::alloy::maxmind_license_key"]
    catalog = compile_catalog(
        tmp_path, "lookup('classes', Array[String]).include", data
    )
    result = resources(catalog)
    config = result["File[/etc/alloy/config.alloy]"]["content"]
    available = mode == "complete"
    assert ("Package[geoipupdate]" in result) == available
    assert ('loki.process "suricata_geoip"' in config) == available
    assert "loki.source.journal" in config
    assert 'loki.source.docker "docker_logs"' in config
    command = str(result["Exec[create_samba_user_printer]"]["command"])
    assert "fixture-printer" in command
    if available:
        geoip = result["File[/etc/GeoIP.conf]"]["content"]
        assert "fixture-license" in geoip
    else:
        assert "File[/etc/GeoIP.conf]" not in result


@pytest.mark.parametrize("refresh", [True, False])
@pytest.mark.parametrize("ensure", ["present", "absent"])
def test_deploy_options(tmp_path: Path, refresh: bool, ensure: str) -> None:
    code = f"""
      include profile::alloy
      profile::docker_deploy {{ 'custom':
        ensure => '{ensure}', scheduled_refresh => {str(refresh).lower()},
        pull => false, branch => 'production', run_as => 'root',
        env_file => 'production.env', compose_file => 'docker/compose.yaml',
        build_command => 'docker compose build', deploy_command => 'docker compose --profile ai up -d',
        watch_dir => 'docker', refresh_calendar => 'Tue *-*-* 04:00:00 UTC',
      }}
    """
    catalog = compile_catalog(tmp_path, code, hostname="fixture", node_type="proxmox")
    result = resources(catalog)
    service = result["File[/etc/systemd/system/custom-deploy.service]"]
    content = service["content"]
    assert service["ensure"] == ("file" if ensure == "present" else "absent")
    assert " compose pull" not in content
    assert content.index("docker compose build") < content.index(
        "docker compose --profile ai up -d"
    )
    assert (
        "/usr/bin/docker compose --env-file production.env -f docker/compose.yaml ps"
        in content
    )
    assert ("docker-compose-health-check" in content) == refresh
    assert result["Service[custom-deploy.path]"]["enable"] == (ensure == "present")
    timer_ref = "File[/etc/systemd/system/custom-refresh.timer]"
    assert (timer_ref in result) == refresh
    if refresh:
        assert "OnCalendar=Tue *-*-* 04:00:00 UTC" in result[timer_ref]["content"]
        assert (
            "ExecCondition="
            not in result["File[/etc/systemd/system/custom-refresh.service]"]["content"]
        )
    baseline = os.environ.get("PROFILE_BASELINE_ROOT")
    if baseline:
        before = compile_catalog(
            tmp_path / "before",
            code,
            hostname="fixture",
            node_type="proxmox",
            root=Path(baseline),
        )
        assert managed_resources(catalog) == managed_resources(before)
        assert ordered_pairs(catalog) == ordered_pairs(before)
        assert notifications(catalog) == notifications(before)


@pytest.mark.parametrize(
    ("parameters", "home"),
    [("username => 'root'", "/root"), ("home => '/srv/user'", "/srv/user")],
)
def test_cursor_guard_resolves_home(tmp_path: Path, parameters: str, home: str) -> None:
    catalog = compile_catalog(
        tmp_path,
        f"class {{ 'profile::cursor_cli': {parameters} }}",
        hostname="fixture",
        node_type="proxmox",
    )
    guard = resources(catalog)["Exec[update-cursor-cli]"]["unless"]
    assert guard == f"/usr/local/sbin/cursor-latest-version --current {home}"
