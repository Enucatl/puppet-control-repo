from __future__ import annotations

from pathlib import Path
import os
import re
import shlex
import subprocess
from typing import Any

import pytest

from puppet_catalog import ROOT, compile_catalog, resources

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


def test_aperture_catalog_uses_atlas_services(tmp_path: Path) -> None:
    """Prepare the new desktop with native Atlas identity, storage and TLS."""
    catalog = compile_catalog(
        tmp_path,
        (ROOT / "manifests/site.pp").read_text(),
        DUMMY_SECRETS,
        hostname="aperture",
        node_type="desktop",
        fact_overrides={
            "os": {
                "family": "Debian",
                "name": "Ubuntu",
                "architecture": "amd64",
                "release": {"major": "26.04", "full": "26.04"},
                "distro": {"codename": "resolute"},
            }
        },
    )
    result = resources(catalog)
    documents = result["File[/home/user/Documents]"]
    assert documents["ensure"] == "directory"
    assert documents["owner"] == documents["group"] == "user"
    assert documents["mode"] == "0750"
    pairs = ordered_pairs(catalog)
    assert ("File[/home/user]", "File[/home/user/Documents]") in pairs
    assert (
        "File[/home/user/Documents]",
        "Posix_acl[/home/user/Documents]",
    ) in pairs
    documents_mount = next(
        name
        for name, parameters in result.items()
        if name.startswith("Mount[")
        and parameters.get("device") == "/home/user/Documents"
    )
    assert ("File[/home/user/Documents]", documents_mount) in pairs
    shim = result["File[/usr/local/libexec/openvox-child-policy.rb]"]
    assert shim["source"] == "puppet:///modules/profile/openvox-child-policy.rb"
    assert shim["mode"] == "0644"
    crypto = result["File[/etc/systemd/system/puppet.service.d/puppet-crypto.conf]"][
        "content"
    ]
    assert "-r /usr/local/libexec/openvox-child-policy.rb" in crypto
    assert "OPENSSL_CONF=/etc/puppetlabs/puppet/openssl.cnf" in crypto
    enrollment = result["Freeipa_client_enrollment[default]"]
    assert enrollment["server"] == "ipa.home.arpa"
    assert enrollment["hostname"] == "aperture.home.arpa"
    assert enrollment["ca_cert_file"] == (
        "/usr/local/share/ca-certificates/atlas-infrastructure.crt"
    )
    certificate = result["Vault_cert[codex]"]
    assert certificate["vault_uri"] == (
        "https://vault.home.arpa:8200/v1/pki_int/issue/puppet"
    )
    assert certificate["ca_trust"] == "/etc/puppetlabs/puppet/ssl/certs/ca.pem"
    assert certificate["cert_data"]["common_name"] == "codex.aperture.home.arpa"
    for share in ("scratch", "docker"):
        mount = result[f"Nfs::Client::Mount[/export/{share}]"]
        assert mount["server"] == "atlas.home.arpa"
        assert mount["atboot"] is False
        assert "sec=krb5p" in mount["options_nfsv4"]
        assert "proto=tcp" in mount["options_nfsv4"]
        assert "x-systemd.automount" in mount["options_nfsv4"]
    export = result["Nfs::Server::Export[/home/user/Documents]"]
    assert export["clients"] == (
        "atlas.home.arpa(sec=krb5p,ro,sync,no_subtree_check,root_squash)"
    )
    assert result["Nfs_config::Ipa_service[nfs]"]["ipa_server"] == "ipa.home.arpa"
    assert "loki.atlas.home.arpa" in result["File[/etc/alloy/config.alloy]"]["content"]
    assert "Class[Profile::Codex_plugins]" in result
    assert "Class[Profile::Docker_node]" not in result
    assert "Class[Profile::Vault_login]" not in result
    assert "Nfs::Client::Mount[/export/complex]" not in result


def test_atlas_catalog_uses_new_infrastructure(tmp_path: Path) -> None:
    """Enroll IPA before user resources and issue Atlas certificates on new Vault."""
    catalog = compile_catalog(
        tmp_path,
        (ROOT / "manifests/site.pp").read_text(),
        DUMMY_SECRETS,
        hostname="atlas",
        fact_overrides={
            "os": {
                "family": "Debian",
                "name": "Ubuntu",
                "architecture": "amd64",
                "release": {"major": "26.04", "full": "26.04"},
                "distro": {"codename": "resolute"},
            }
        },
    )
    result = resources(catalog)
    alloy_config = result["File[/etc/alloy/config.alloy]"]["content"]
    assert 'loki.source.docker "goflow2_logs"' in alloy_config
    assert 'listen_address = "10.0.0.158"' in alloy_config
    assert 'host = "atlas.home.arpa"' in alloy_config
    ssh_config = result["Concat::Fragment[ssh_config global config]"]["content"]
    assert "ProxyCommand none\n" in ssh_config
    assert "KnownHostsCommand /usr/bin/sss_ssh_knownhosts %H\n" in ssh_config
    assert "sss_ssh_knownhostsproxy" not in ssh_config
    shim = result["File[/usr/local/libexec/openvox-child-policy.rb]"]
    assert shim["source"] == "puppet:///modules/profile/openvox-child-policy.rb"
    assert shim["mode"] == "0644"
    crypto = result["File[/etc/systemd/system/puppet.service.d/puppet-crypto.conf]"][
        "content"
    ]
    assert "-r /usr/local/libexec/openvox-child-policy.rb" in crypto
    assert "OPENSSL_CONF=/etc/puppetlabs/puppet/openssl.cnf" in crypto
    assert result["File[/etc/apt/keyrings/grafana-alloy-archive-keyring.asc]"][
        "source"
    ] == ("puppet:///modules/profile/grafana-signing-key.asc")
    archive = result["Archive[/var/cache/beszel-agent/beszel-agent_linux_amd64.tar.gz]"]
    assert archive["provider"] == "wget"
    assert not archive.get("allow_insecure", False)
    assert "Package[wget]" in result
    enrollment = result["Freeipa_client_enrollment[default]"]
    assert enrollment["server"] == "ipa.home.arpa"
    assert enrollment["hostname"] == "atlas.home.arpa"
    assert enrollment["ca_cert_file"] == (
        "/usr/local/share/ca-certificates/atlas-infrastructure.crt"
    )
    assert (
        "set target[.='domain/home.arpa']/ipa_server ipa.home.arpa"
        in (result["Augeas[sssd_krb5_renewal]"]["changes"])
    )
    for name in ("atlas", "airflow", "wolf", "browser-use", "codex"):
        certificate = result[f"Vault_cert[{name}]"]
        assert certificate["vault_uri"] == (
            "https://vault.home.arpa:8200/v1/pki_int/issue/puppet-atlas.home.arpa"
        )
        assert certificate["ca_trust"] == "/etc/puppetlabs/puppet/ssl/certs/ca.pem"
        assert "atlas.home.arpa" in certificate["cert_data"]["common_name"]
        if name != "codex":
            for key in ("cert_chain_file", "key_file"):
                directory = Path(certificate[key]).parent
                assert result[f"File[{directory}]"]["ensure"] == "directory"
    assert (
        result["Vault_cert[atlas]"]["cert_data"]["common_name"] == "*.atlas.home.arpa"
    )
    assert "Class[Profile::Docker_node]" not in result
    assert "Class[Profile::Paperless_scans]" in result
    share = result["Samba::Share[scans]"]
    assert share["path"] == "/opt/paperless-consume"
    assert share["writable"] is True
    assert share["valid_users"] == ["printer"]
    assert share["force_user"] == "printer"
    samba = result["Class[Samba]"]
    assert "10.0.20.189" in samba["hosts_allow"]
    assert "fd6b:d722:1738:0::/64" in samba["hosts_allow"]
    assert "fd6b:d722:1738:28::/64" in samba["hosts_allow"]
    scratch = result["Mount[/scratch]"]
    assert scratch["ensure"] == "mounted"
    assert scratch["device"] == "UUID=99b387e9-afd5-4e15-bcbe-b471275fbb07"
    assert scratch["fstype"] == "ext4"
    assert scratch["atboot"] is True
    assert result["Nfs::Server::Export[/scratch]"]["atboot"] is True
    assert result["Samba::Share[shared_pictures]"]["path"] == "/scratch/shared_pictures"
    assert (
        "RequiresMountsFor=/scratch"
        in result["File[/etc/systemd/system/docker.service.d/scratch.conf]"]["content"]
    )
    assert samba["hosts_deny"] == ["ALL"]
    consume = result["File[/opt/paperless-consume]"]
    assert consume["ensure"] == "directory"
    assert consume["owner"] == consume["group"] == "printer"
    printer = result["Exec[create_samba_user_printer]"]
    assert str(printer["command"]).startswith("/usr/bin/printf '%s\\n%s\\n'")
    assert "fixture-printer" in str(printer["command"])
    assert "smbpasswd -a -s printer" in str(printer["command"])
    assert printer["unless"] == "/usr/bin/pdbedit -L -u printer"
    assert printer["require"] == "Class[Samba::Install]"
    for name in ("airflow", "traefik", "paperless-ai", "grindr-api-dev-tool"):
        directory = result[f"File[/opt/docker/{name}]"]
        assert directory["ensure"] == "directory"
        assert directory["owner"] == "user"
        assert directory["group"] == "docker"
        assert directory["mode"] == "2770"
        assert not directory.get("recurse", False)
    assert "File[/opt/docker/compose-security-baseline]" not in result
    pairs = ordered_pairs(catalog)
    assert result["Exec[sync-user-toolchain-user]"]["cwd"] == "/home/user"
    assert ("File[/home/user]", "Exec[sync-user-toolchain-user]") in pairs
    assert ("Exec[apt_update]", "Package[alloy]") in pairs
    apt_errors = result["File[/etc/apt/apt.conf.d/50update-errors]"]
    assert 'APT::Update::Error-Mode "any";' in apt_errors["content"]
    assert apt_errors["notify"] == "Class[Apt::Update]"
    for resource in (
        "Exec[add_ipa_user_user_to_groups]",
        "File[/etc/beszel-agent]",
        "File[/var/cache/beszel-agent]",
        "File[/var/lib/beszel-agent]",
        "File[/etc/beszel-agent/key]",
        "File[/etc/beszel-agent/token]",
        "Service[beszel-agent.service]",
        "Vault_cert[codex]",
        "File[/home/user]",
        "File[/home/user/.config/home-arpa/mtls]",
        "Exec[sync-user-toolchain-user]",
        "Exec[sync-codex-plugins-user]",
        "Cron[user-toolchain-cache-prune]",
        "Concat_file[/etc/ssh/ssh_config]",
    ):
        assert ("Freeipa_client_enrollment[default]", resource) in pairs


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
    assert "File[/usr/local/libexec/openvox-child-policy.rb]" not in result
    assert "File[/etc/systemd/system/puppet.service.d/puppet-crypto.conf]" not in result
    if node_type in {"docker", "desktop"}:
        ssh_config = result["Concat::Fragment[ssh_config global config]"]["content"]
        assert "ProxyCommand /usr/bin/sss_ssh_knownhostsproxy -p %p %h\n" in ssh_config
    assert ("File[/usr/local/bin/typst]" in result) == (node_type == "desktop")
    assert ("Cron[typst-update]" in result) == (node_type == "desktop")
    certificate_role = "puppet-docker.home.arpa" if hostname == "docker" else "puppet"
    for resource_name, parameters in result.items():
        if resource_name.startswith("Vault_cert["):
            assert parameters["vault_uri"] == (
                f"https://hcv.home.arpa:8200/v1/pki_int/issue/{certificate_role}"
            )
            assert parameters["auth_name"] == "puppet"
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
    "certname", ["forbearance.home.arpa", "docker.example.org", "ordinary.home.arpa"]
)
def test_forged_role_cannot_retrieve_docker_secrets(
    tmp_path: Path, certname: str
) -> None:
    """Authorize catalog secrets by certificate despite forged role/host facts."""
    catalog = compile_catalog(
        tmp_path,
        (ROOT / "manifests/site.pp").read_text(),
        DUMMY_SECRETS,
        hostname="docker",
        node_type="docker",
        certname=certname,
    )
    serialized = str(catalog)
    for secret in ("fixture-license", "fixture-printer", "fixture-pictures"):
        assert secret not in serialized
    result = resources(catalog)
    assert "Class[Profile::Docker_host]" not in result
    assert "Class[Profile::Docker_node]" not in result
    assert "File[/etc/GeoIP.conf]" not in result


@pytest.mark.parametrize("field", ["family", "name"])
def test_os_hierarchy_rejects_fact_path_traversal(tmp_path: Path, field: str) -> None:
    """Reject facts that try to load privileged Hiera files through OS paths."""
    with pytest.raises(AssertionError, match="OS hierarchy facts must be simple names"):
        compile_catalog(
            tmp_path,
            (ROOT / "manifests/site.pp").read_text(),
            DUMMY_SECRETS,
            certname="ordinary.home.arpa",
            fact_overrides={
                "os": {"family": "Debian", "name": "Ubuntu", field: "../roles/docker"}
            },
        )


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


def test_grafana_public_key_matches_published_primary_fingerprint(
    tmp_path: Path,
) -> None:
    """Keep package authentication pinned to Grafana's published signing identity."""
    output = subprocess.check_output(
        [
            "gpg",
            "--batch",
            "--homedir",
            str(tmp_path),
            "--show-keys",
            "--with-colons",
            str(ROOT / "modules/profile/files/grafana-signing-key.asc"),
        ],
        text=True,
    )
    primary = output.split("fpr:", 1)[1].splitlines()[0].split(":")[-2]
    assert primary == "B53AE77BADB630A683046005963FA27710458545"
