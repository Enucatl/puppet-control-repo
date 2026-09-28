from __future__ import annotations

from pathlib import Path
import os
import re
import shlex
from typing import Any

import pytest

from puppet_catalog import compile_catalog, resources

DUMMY_SECRETS = {
    "freeipa::client::password": "fixture-password",
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
    assert "Vcsrepo[/root/.vim]" not in result
    home = result["File[/home/user]"]
    assert home["ensure"] == "directory"
    assert home["owner"] == home["group"] == "user"
    assert home["mode"] == "0700"
    assert ("File[/home/user]", "Vcsrepo[/home/user/.vim]") in ordered_pairs(catalog)
    assert result["Vcsrepo[/home/user/.vim]"]["user"] == "user"
    assert result["Exec[dotfiles-rake-links]"]["user"] == "user"
    assert result["Exec[dotfiles-rake-links]"]["cwd"] == "/home/user/.vim"
    if node_type == "proxmox":
        assert result["Package[sudo]"]["ensure"] == "installed"
    assert ("Class[Freeipa_users::Provision]" in result) == (hostname == "docker")
    assert ("Exec[ipa-user-provision-backrest]" in result) == (hostname == "docker")
    assert result["File[/run/puppet-ipa-admin-pass]"]["ensure"] == "absent"
    assert "kinit admin" not in str(catalog)
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
        for action in ("deploy", "refresh"):
            unit = result[f"File[/etc/systemd/system/beszel-{action}.service]"][
                "content"
            ]
            assert (
                "ExecStart=/bin/bash -c 'docker compose up -d --force-recreate "
                "--wait --wait-timeout 300 beszel && docker compose run --rm alerts-init'"
                in unit.splitlines()
            )
    if hostname == "complex":
        for project in ("vllm", "wolf"):
            unit = result[f"File[/etc/systemd/system/{project}-deploy.service]"][
                "content"
            ]
            assert "up --force-recreate --wait --wait-timeout 300" in unit
            assert f"File[/etc/systemd/system/{project}-refresh.service]" not in result
    if docker_role:
        assert (
            result["File[/usr/local/sbin/docker-compose-health-check]"]["ensure"]
            == "absent"
        )
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


@pytest.mark.parametrize(
    ("certname", "authenticated"),
    [
        ("complex.home.arpa", "remote"),
        ("docker.example.org", "remote"),
        ("docker.home.arpa", "local"),
        ("docker.home.arpa", "false"),
    ],
)
def test_freeipa_provision_requires_docker_certificate(
    tmp_path: Path, certname: str, authenticated: str
) -> None:
    """Reject provisioning despite Docker hostname and role facts."""
    with pytest.raises(
        AssertionError, match="authenticated docker.home.arpa certificate"
    ):
        compile_catalog(
            tmp_path,
            "include freeipa_users::provision",
            hostname="docker",
            node_type="docker",
            certname=certname,
            authenticated=authenticated,
        )


def test_freeipa_provision_command_arguments(tmp_path: Path) -> None:
    """Preserve argument boundaries and keep keytab contents out of catalogs."""
    first = "First; $(touch /tmp/injected)"
    last = "O'Last"
    catalog = compile_catalog(
        tmp_path,
        "include freeipa_users::provision",
        {
            "freeipa_users::provision::users": {
                "backrest": {
                    "first": first,
                    "last": last,
                    "keytab": "/etc/krb5-backrest.keytab",
                }
            }
        },
    )
    result = resources(catalog)
    command = result["Exec[ipa-user-provision-backrest]"]
    expected = [
        "/usr/local/sbin/puppet-ipa-provision-user",
        "backrest",
        first,
        last,
        "/usr/sbin/nologin",
        "/etc/krb5-backrest.keytab",
    ]
    assert shlex.split(command["command"]) == expected
    assert shlex.split(command["unless"]) == [expected[0], "--check", *expected[1:]]
    for path in ("/etc/puppet-ipa-provisioner.keytab", "/etc/krb5-backrest.keytab"):
        keytab = result[f"File[{path}]"]
        assert keytab["owner"] == keytab["group"] == "root"
        assert keytab["mode"] == "0400"
        assert "source" not in keytab and "content" not in keytab


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


@pytest.mark.parametrize("refresh", [True, False])
@pytest.mark.parametrize(
    ("parameters", "compose", "timeout"),
    [
        ("", "/usr/bin/docker compose", 300),
        (
            "wait_timeout => 45, env_file => 'production.env', "
            "compose_file => 'docker/compose.yaml',",
            "/usr/bin/docker compose --env-file production.env -f docker/compose.yaml",
            45,
        ),
    ],
)
def test_docker_deploy_catalog(
    tmp_path: Path, refresh: bool, parameters: str, compose: str, timeout: int
) -> None:
    """Wait for readiness in both units, independently of scheduled refresh."""
    catalog = compile_catalog(
        tmp_path,
        f"""
        include profile::alloy
        profile::docker_deploy {{ 'example':
          scheduled_refresh => {str(refresh).lower()}, watch_dir => 'docker',
          build_command => 'docker compose build', {parameters}
        }}
        """,
        hostname="fixture",
        node_type="proxmox",
    )
    result = resources(catalog)
    assert (
        result["File[/usr/local/sbin/docker-compose-health-check]"]["ensure"]
        == "absent"
    )
    assert ("File[/etc/systemd/system/example-refresh.service]" in result) == refresh
    for action in ("deploy", "refresh") if refresh else ("deploy",):
        unit = result[f"File[/etc/systemd/system/example-{action}.service]"]["content"]
        starts = [line for line in unit.splitlines() if line.startswith("ExecStart=")]
        assert starts == [
            f"ExecStart={compose} pull",
            "ExecStart=/bin/bash -c 'docker compose build'",
            f"ExecStart={compose} up --force-recreate --wait --wait-timeout {timeout}",
        ]
        assert ("ExecCondition=" in unit) == (action == "deploy")
        if action == "deploy":
            assert "git diff --name-only HEAD@{1} HEAD -- docker | grep -q ." in unit
        assert f"ExecStartPost={compose} ps" in unit.splitlines()
        assert "docker-compose-health-check" not in unit
        assert "Environment=COMPOSE_ENV_FILES=../.env,./.env" in unit.splitlines()


def test_base_alloy_catalog(tmp_path: Path) -> None:
    catalog = compile_catalog(
        tmp_path, "include profile::alloy", hostname="proxmox", node_type="proxmox"
    )
    result = resources(catalog)
    assert "Service[alloy]" in result


def test_codex_plugins_catalog(tmp_path: Path) -> None:
    catalog = compile_catalog(
        tmp_path,
        """
        exec { 'sync-user-toolchain-user': command => '/bin/true' }
        exec { 'dotfiles-rake-links': command => '/bin/true' }
        include profile::codex_plugins
        """,
    )
    result = resources(catalog)
    script = result["File[/usr/local/sbin/puppet-codex-plugins-sync]"]
    assert script["source"] == "puppet:///modules/profile/codex-plugins-sync"
    assert result["Exec[sync-codex-plugins-user]"]["user"] == "user"
    pairs = ordered_pairs(catalog)
    assert ("Exec[sync-user-toolchain-user]", "Exec[sync-codex-plugins-user]") in pairs
    assert ("Exec[dotfiles-rake-links]", "Exec[sync-codex-plugins-user]") in pairs


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
        wait_timeout => 45,
        watch_dir => 'docker', refresh_calendar => 'Tue *-*-* 04:00:00 UTC',
      }}
    """
    catalog = compile_catalog(tmp_path, code, hostname="fixture", node_type="proxmox")
    result = resources(catalog)
    service = result["File[/etc/systemd/system/custom-deploy.service]"]
    assert service["ensure"] == ("file" if ensure == "present" else "absent")
    for action in ("deploy", "refresh") if refresh else ("deploy",):
        unit = result[f"File[/etc/systemd/system/custom-{action}.service]"]["content"]
        starts = [line for line in unit.splitlines() if line.startswith("ExecStart=")]
        assert starts == [
            "ExecStart=/bin/bash -c 'docker compose build'",
            "ExecStart=/bin/bash -c 'docker compose --profile ai up -d'",
        ]
        assert (
            "ExecStartPost=/usr/bin/docker compose --env-file production.env "
            "-f docker/compose.yaml ps" in unit.splitlines()
        )
        assert "--wait" not in unit
        assert "docker-compose-health-check" not in unit
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
