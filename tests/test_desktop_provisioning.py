"""Exercise desktop provisioning without Vault access or real virtual machines."""

from __future__ import annotations

import importlib
import json
import subprocess
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock
from urllib.parse import quote

import pytest


PROXMOX = Path(__file__).resolve().parents[1] / "proxmox"
CERTNAME = "desktop.home.arpa"
AUTH = {
    "client_token": "dummy-enrollment-token",
    "policies": ["puppet-enrollment"],
    "metadata": {"certname": CERTNAME},
    "token_type": "service",
    "orphan": True,
    "renewable": False,
    "num_uses": 1,
    "lease_duration": 7200,
}


@pytest.fixture
def desktop(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Import the executable's sibling modules using its normal import path."""
    monkeypatch.syspath_prepend(str(PROXMOX))
    module = importlib.import_module("desktop")
    monkeypatch.setattr(module.platform, "node", lambda: "pve.home.arpa")
    return module


def test_cli_preserves_defaults_and_short_options(desktop: ModuleType) -> None:
    """Keep existing desktop.sh invocations usable after migration."""
    defaults = desktop.parse_args([])
    assert (defaults.letter, defaults.vmid) == ("", None)
    assert (defaults.cores, defaults.memory, defaults.disk_size) == (8, 8000, "128G")
    explicit = desktop.parse_args(
        ["-l", "d", "-i", "123", "-c", "4", "-m", "4096", "-d", "64G"]
    )
    assert (explicit.letter, explicit.vmid) == ("d", "123")
    assert (explicit.cores, explicit.memory, explicit.disk_size) == (4, 4096, "64G")


def test_settings_preserve_quoted_literals(
    desktop: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Read values with spaces and shell syntax without executing or expanding them."""
    monkeypatch.setattr(desktop, "SCRIPT_DIR", tmp_path)
    (tmp_path / "config.sh").write_text(
        'DOMAIN_SUFFIX="home.arpa"\nPUPPET_SERVER="docker.${DOMAIN_SUFFIX}"\n'
        'DEFAULT_STORAGE="local-zfs"\nSNIPPET_STORAGE="local"\nTEMPLATE_ID=9000\n'
    )
    marker = tmp_path / "must-not-exist"
    token = f"literal $dollar $(touch {marker})"
    env = tmp_path / ".env"
    env.write_text(
        f"VAULT_TOKEN='{token}'\n"
        'VAULT_ADDR="https://vault.example:8200"\n'
        f'LOG_DIR="{tmp_path / "logs with spaces"}"\n'
        'VAULT_CACERT="/tmp/custom ca.pem" # comment\n'
        'OPENVOX_CA_CERT="/tmp/authenticated-openvox-ca.pem"\n'
    )
    settings = desktop.load_settings(env)
    assert settings["VAULT_TOKEN"] == token
    assert settings["PUPPET_SERVER"] == "docker.home.arpa"
    assert settings["LOG_DIR"] == str(tmp_path / "logs with spaces")
    assert settings["VAULT_CACERT"] == "/tmp/custom ca.pem"
    assert not marker.exists()


@pytest.mark.parametrize(
    "args", [["-l", "a.*"], ["-i", "0"], ["-c", "0"], ["-m", "-1"], ["-d", "--help"]]
)
def test_cli_rejects_invalid_parameters(desktop: ModuleType, args: list[str]) -> None:
    """Reject invalid arguments before they can reach the VM commands."""
    with pytest.raises(SystemExit) as error:
        desktop.parse_args(args)
    assert error.value.code == 2


@pytest.mark.parametrize("vmid", ["9000", "not-an-id"])
def test_auto_vmid_cannot_overwrite_template(
    desktop: ModuleType, monkeypatch: pytest.MonkeyPatch, vmid: str
) -> None:
    """Validate IDs from Proxmox as well as IDs supplied through the CLI."""
    run = Mock(return_value=subprocess.CompletedProcess([], 0, vmid + "\n", ""))
    monkeypatch.setattr(desktop, "run", run)
    with pytest.raises(ValueError, match="VMID"):
        desktop.provision(desktop.parse_args([]), {"TEMPLATE_ID": "9000"})
    run.assert_called_once_with("pvesh", "get", "/cluster/nextid")


@pytest.mark.parametrize("ca", [None, "/tmp/custom ca.pem"])
def test_vault_request_is_restricted_and_verified(
    desktop: ModuleType, monkeypatch: pytest.MonkeyPatch, ca: str | None
) -> None:
    """Issue an orphan token without leaking inherited policies or disabling TLS."""
    post = Mock(
        return_value=SimpleNamespace(
            raise_for_status=Mock(), json=lambda: {"auth": AUTH}, status_code=200
        )
    )
    monkeypatch.setattr(desktop.niquests, "post", post)
    settings = {"VAULT_ADDR": "https://vault.example:8200", "VAULT_TOKEN": "issuer"}
    if ca:
        settings["VAULT_CACERT"] = ca
    assert desktop.create_vault_token(settings, CERTNAME) == AUTH["client_token"]
    post.assert_called_once_with(
        "https://vault.example:8200/v1/auth/token/create-orphan",
        headers={"X-Vault-Token": "issuer"},
        json={
            "policies": ["puppet-enrollment"],
            "type": "service",
            "ttl": "2h",
            "num_uses": 1,
            "renewable": False,
            "no_default_policy": True,
            "meta": {"certname": CERTNAME},
        },
        verify=ca or True,
        timeout=30,
        allow_redirects=False,
    )


@pytest.mark.parametrize(
    "changed",
    [
        {"client_token": ""},
        {"client_token": None},
        {"policies": ["default", "puppet-enrollment"]},
        {"metadata": {"certname": "other.home.arpa"}},
        {"metadata": None},
        {"token_type": "batch"},
        {"orphan": False},
        {"renewable": True},
        {"num_uses": 0},
        {"num_uses": True},
        {"lease_duration": 0},
        {"lease_duration": 7201},
        {"lease_duration": "7200"},
        {"lease_duration": True},
    ],
)
def test_vault_rejects_unrestricted_or_malformed_tokens(
    desktop: ModuleType, monkeypatch: pytest.MonkeyPatch, changed: dict[str, object]
) -> None:
    """Fail closed when a response changes any enrollment restriction."""
    monkeypatch.setattr(
        desktop.niquests,
        "post",
        Mock(
            return_value=SimpleNamespace(
                raise_for_status=Mock(),
                json=lambda: {"auth": {**AUTH, **changed}},
                status_code=200,
            )
        ),
    )
    with pytest.raises(ValueError, match="Invalid Vault enrollment"):
        desktop.create_vault_token(
            {"VAULT_ADDR": "https://vault.example", "VAULT_TOKEN": "issuer"},
            CERTNAME,
        )


def test_command_arguments_do_not_pass_through_a_shell(desktop: ModuleType) -> None:
    """Subprocess arguments retain spaces, dollars, and quotes verbatim."""
    value = 'literal $HOME "quote" ; exit 9'
    result = desktop.run("printf", "%s", value)
    assert result.stdout == value


@pytest.mark.parametrize("failed", [False, True])
def test_cloudinit_readiness_and_diagnostics(
    desktop: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failed: bool,
) -> None:
    """Retry an unavailable guest agent and surface cloud-init failure details."""
    commands: list[tuple[str, ...]] = []
    sleeps: list[int] = []

    def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        """Return an agent failure, running status, and then a terminal status."""
        commands.append(args)
        if len(commands) == 1:
            if check:
                raise subprocess.CalledProcessError(1, args)
            return subprocess.CompletedProcess(args, 1, "", "not ready")
        if args[-2:] == ("cloud-init", "status"):
            state = "running" if len(commands) == 2 else "error" if failed else "done"
            output = f"status: {state}"
        else:
            output = "guest failure details"
        return subprocess.CompletedProcess(
            args, 0, json.dumps({"out-data": output}), ""
        )

    monkeypatch.setattr(desktop, "run", run)
    monkeypatch.setattr(desktop.time, "sleep", sleeps.append)
    if failed:
        with pytest.raises(RuntimeError):
            desktop.wait_for_cloudinit("123")
        assert "guest failure details" in capsys.readouterr().err
        assert any(
            command[-3:] == ("cloud-init", "status", "--long") for command in commands
        )
        assert any("journalctl" in " ".join(command) for command in commands)
    else:
        desktop.wait_for_cloudinit("123")
    assert len(sleeps) == 2


@pytest.mark.parametrize("rebuild", [False, True])
@pytest.mark.parametrize("failure", [None, "wait", "reboot", "token", "lost-ownership"])
def test_provision_lifecycle_and_cleanup(
    desktop: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str | None,
    rebuild: bool,
) -> None:
    """Clean failed clones while preserving configured VMs after reboot failures."""
    commands: list[tuple[str, ...]] = []
    snippets = tmp_path / "snippets"
    snippets.mkdir()
    logs = tmp_path / "logs"
    logs.mkdir()
    ca_bundle = tmp_path / "authenticated-ca.pem"
    ca_bundle.write_text(
        "-----BEGIN CERTIFICATE-----\npublic fixture\n-----END CERTIFICATE-----\n"
    )
    settings = {
        "DOMAIN_SUFFIX": "home.arpa",
        "PUPPET_SERVER": "puppet.home.arpa",
        "VAULT_ADDR": "https://vault.example",
        "VAULT_TOKEN": "issuer",
        "DEFAULT_STORAGE": "local-zfs",
        "SNIPPET_STORAGE": "local",
        "TEMPLATE_ID": "9000",
        "LOG_DIR": str(logs),
        "OPENVOX_CA_CERT": str(ca_bundle),
    }

    def issue(_settings: dict[str, str], certname: str) -> str:
        """Require token validation before any destructive Proxmox command."""
        assert certname == CERTNAME
        assert not any(
            command[:2] in (("qm", "clone"), ("qm", "destroy")) for command in commands
        )
        if failure == "token":
            raise RuntimeError("token failed")
        return str(AUTH["client_token"])

    def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        """Record Proxmox commands without modifying a host."""
        commands.append(args)
        if args[:3] == ("pvesh", "get", "/cluster/resources"):
            resources = (
                [{"vmid": 123, "type": "qemu", "name": "desktop", "node": "pve"}]
                if rebuild
                else []
            )
            resources.append(
                {"vmid": 9000, "type": "qemu", "name": "template", "node": "pve"}
            )
            return subprocess.CompletedProcess(args, 0, json.dumps(resources), "")
        if args == ("qm", "config", "9000"):
            return subprocess.CompletedProcess(args, 0, "template: 1\n", "")
        if args[:2] == ("qm", "config"):
            return subprocess.CompletedProcess(
                args,
                0,
                "name: unrelated\ndescription: other\n"
                if failure == "lost-ownership"
                and any(command[:2] == ("qm", "clone") for command in commands)
                else "name: desktop\ndescription: openvox-provisioning:desktop:desktop.home.arpa\n",
                "",
            )
        if args[-1] == "hostname":
            return subprocess.CompletedProcess(
                args, 0, json.dumps({"exitcode": 0, "out-data": "desktop\n"}), ""
            )
        if args[-2:] == ("hostname", "-f"):
            return subprocess.CompletedProcess(
                args, 0, json.dumps({"exitcode": 0, "out-data": CERTNAME + "\n"}), ""
            )
        if args[-1] == "/proc/sys/kernel/random/boot_id":
            boot = (
                "after"
                if any(command[:2] == ("qm", "reboot") for command in commands)
                else "before"
            )
            return subprocess.CompletedProcess(
                args, 0, json.dumps({"exitcode": 0, "out-data": boot}), ""
            )
        if args == ("qm", "set", "123", "--delete", "cicustom"):
            assert (snippets / "generated-desktop-123.yml").exists()
        if args[:2] == ("qm", "reboot"):
            assert ("qm", "set", "123", "--delete", "cicustom") in commands
            assert not (snippets / "generated-desktop-123.yml").exists()
            if failure == "reboot":
                raise RuntimeError("reboot failed")
        if args[:2] == ("qm", "clone"):
            snippet = snippets / "generated-desktop-123.yml"
            assert str(AUTH["client_token"]) in snippet.read_text()
            assert snippet.stat().st_mode & 0o077 == 0
        return subprocess.CompletedProcess(
            args,
            0,
            json.dumps({"exitcode": 0, "out-data": "provisioned guest log"}),
            "",
        )

    def wait(vmid: str) -> None:
        """Simulate the guest's terminal provisioning state."""
        assert vmid == "123"
        if failure in ("wait", "lost-ownership"):
            raise RuntimeError(f"{failure} failed")

    monkeypatch.setattr(desktop, "SNIPPET_DIR", snippets)
    monkeypatch.setattr(desktop, "pick_vm_name", lambda letter: "desktop")
    monkeypatch.setattr(desktop, "create_vault_token", issue)
    monkeypatch.setattr(desktop, "run", run)
    monkeypatch.setattr(desktop, "wait_for_cloudinit", wait)
    monkeypatch.setattr(desktop.time, "sleep", lambda _: None)
    args = desktop.parse_args(
        ["-i", "123"] + (["-n", "desktop", "--rebuild"] if rebuild else [])
    )
    if failure:
        with pytest.raises(RuntimeError, match=f"{failure} failed"):
            desktop.provision(args, settings)
    else:
        assert desktop.provision(args, settings) == "desktop"
    assert list(snippets.iterdir()) == []
    if failure == "token":
        assert len(commands) == (3 if rebuild else 2) and commands[0][:3] == (
            "pvesh",
            "get",
            "/cluster/resources",
        )
        return
    clone = commands.index(
        (
            "qm",
            "clone",
            "9000",
            "123",
            "--name",
            "desktop",
            "--storage",
            "local-zfs",
            "--full",
            "1",
            "--description",
            "openvox-provisioning:desktop:desktop.home.arpa",
        )
    )
    assert (("qm", "destroy", "123", "--purge") in commands[:clone]) is rebuild
    assert ("qm", "resize", "123", "scsi0", "128G") in commands
    assert (
        "qm",
        "set",
        "123",
        "--agent",
        "1",
        "--cores",
        "8",
        "--memory",
        "8000",
        "--cpu",
        "host",
    ) in commands
    assert (
        "qm",
        "set",
        "123",
        "--machine",
        "q35",
        "--bios",
        "ovmf",
        "--vga",
        "virtio",
    ) in commands
    assert not any(
        "--hostpci" in argument for command in commands for argument in command
    )
    assert (
        "qm",
        "set",
        "123",
        "--efidisk0",
        "local-zfs:0,efitype=4m,pre-enrolled-keys=1",
    ) in commands
    assert (
        "qm",
        "set",
        "123",
        "--cicustom",
        "vendor=local:snippets/generated-desktop-123.yml",
        "--ipconfig0",
        "ip=dhcp",
        "--searchdomain",
        "home.arpa",
    ) in commands
    if failure == "wait":
        diagnostics = next(logs.glob("desktop-123-failure-*"))
        assert diagnostics.stat().st_mode & 0o077 == 0
        diagnostic = diagnostics / "cloud-init-output.json"
        assert diagnostic.stat().st_mode & 0o077 == 0
        assert "provisioned guest log" in json.loads(diagnostic.read_text())["stdout"]
        assert commands.index(
            (
                "qm",
                "guest",
                "exec",
                "123",
                "--",
                "tail",
                "-n",
                "300",
                "/var/log/cloud-init-output.log",
            )
        ) < commands.index(("qm", "stop", "123"), clone)
        assert commands[-2:] == [
            ("qm", "stop", "123"),
            ("qm", "destroy", "123", "--purge"),
        ]
    else:
        assert not any(
            command[:2] == ("qm", "destroy") for command in commands[clone + 1 :]
        )
        if failure == "lost-ownership":
            assert commands[-1] == ("qm", "config", "123")
            return
        assert ("qm", "reboot", "123") in commands
        if failure is None:
            assert commands[-1] == (
                "qm",
                "guest",
                "exec",
                "123",
                "--",
                "hostname",
                "-f",
            )
        assert "provisioned guest log" in (logs / "cloud-init-output.log").read_text()


@pytest.mark.parametrize(
    "options",
    [
        ["-n", "atlas.home.arpa"],
        ["-n", "bad..name"],
        ["-t", "0"],
        ["-n", "atlas", "-l", "a"],
        ["--rebuild", "-i", "210"],
    ],
)
def test_cli_rejects_ambiguous_identity(
    desktop: ModuleType, options: list[str]
) -> None:
    """Require an exact short hostname and explicit rebuild identity."""
    with pytest.raises(SystemExit):
        desktop.parse_args(options)


def test_explicit_identity_and_endpoint(desktop: ModuleType) -> None:
    """Accept development selection without altering shared production defaults."""
    args = desktop.parse_args(
        ["-n", "atlas", "-i", "210", "-t", "9001", "--puppet-server", "atlas.home.arpa"]
    )
    assert (args.name, args.vmid, args.template_id, args.puppet_server) == (
        "atlas",
        "210",
        "9001",
        "atlas.home.arpa",
    )


@pytest.mark.parametrize("vmid", ["9000", "9001"])
def test_all_template_ids_are_protected(
    desktop: ModuleType, monkeypatch: pytest.MonkeyPatch, vmid: str
) -> None:
    """Protect the legacy image and the selected new image before any host command."""
    run = Mock()
    monkeypatch.setattr(desktop, "run", run)
    with pytest.raises(ValueError, match="VMID"):
        desktop.provision(desktop.parse_args(["-i", vmid, "-t", "9001"]), {})
    run.assert_not_called()


@pytest.mark.parametrize(
    "resource",
    [
        {"vmid": 210, "type": "lxc", "name": "atlas", "node": "remote"},
        {"vmid": 210, "type": "qemu", "name": "unrelated", "node": "remote"},
        {"vmid": 210, "type": "qemu", "name": "atlas", "template": 1},
        {"vmid": 210, "type": "qemu", "name": "atlas", "node": "remote"},
    ],
)
def test_occupied_id_is_rejected_before_token_and_snippet(
    desktop: ModuleType, monkeypatch: pytest.MonkeyPatch, resource: dict[str, object]
) -> None:
    """Cluster-wide collisions cannot cause Vault issuance or destructive calls."""
    run = Mock(
        return_value=subprocess.CompletedProcess([], 0, json.dumps([resource]), "")
    )
    token = Mock()
    monkeypatch.setattr(desktop, "run", run)
    monkeypatch.setattr(desktop, "create_vault_token", token)
    with pytest.raises(ValueError, match="already in use"):
        desktop.provision(
            desktop.parse_args(["-n", "atlas", "-i", "210"]),
            {"TEMPLATE_ID": "9001", "DOMAIN_SUFFIX": "home.arpa"},
        )
    token.assert_not_called()
    assert run.call_count == 1


@pytest.mark.parametrize(
    "description",
    [
        "",
        "openvox-provisioning:desktop:other.home.arpa",
        "openvox-provisioning%3Adesktop%3Aatlas.home.arpa-tampered",
        "openvox-provisioning%253Adesktop%253Aatlas.home.arpa",
    ],
)
def test_rebuild_requires_exact_ownership(
    desktop: ModuleType, monkeypatch: pytest.MonkeyPatch, description: str
) -> None:
    """Even a matching VM name is insufficient authorization for replacement."""
    run = Mock(
        side_effect=[
            subprocess.CompletedProcess(
                [],
                0,
                json.dumps(
                    [{"vmid": 210, "type": "qemu", "name": "atlas", "node": "pve"}]
                ),
                "",
            ),
            subprocess.CompletedProcess(
                [], 0, f"name: atlas\ndescription: {description}\n", ""
            ),
        ]
    )
    monkeypatch.setattr(desktop, "run", run)
    token = Mock()
    monkeypatch.setattr(desktop, "create_vault_token", token)
    with pytest.raises(ValueError, match="script-owned"):
        desktop.provision(
            desktop.parse_args(["-n", "atlas", "-i", "210", "--rebuild"]),
            {"TEMPLATE_ID": "9001", "DOMAIN_SUFFIX": "home.arpa"},
        )
    token.assert_not_called()
    assert run.call_count == 2


def test_cloud_config_preserves_short_and_fully_qualified_identity(
    desktop: ModuleType,
) -> None:
    """Cloud-init owns /etc/hosts and persists the same identity across reboots."""
    config = desktop.build_cloud_config(
        "token", "atlas.home.arpa", "docker.home.arpa", "home.arpa", "ca"
    )
    expected = {
        "hostname": "atlas",
        "fqdn": "atlas.home.arpa",
        "preserve_hostname": False,
        "prefer_fqdn_over_hostname": False,
        "manage_etc_hosts": True,
    }
    assert {key: config[key] for key in expected} == expected
    persistent = next(
        file
        for file in config["write_files"]
        if file["path"] == "/etc/cloud/cloud.cfg.d/99-hostname.cfg"
    )
    assert json.loads(persistent["content"]) == expected
    assert not any(
        isinstance(command, list) and command[0] == "sed"
        for command in config["runcmd"]
    )
    assert [
        "/usr/local/bin/configure-puppet.sh",
        "desktop",
        "token",
        "atlas.home.arpa",
        "docker.home.arpa",
    ] in config["runcmd"]


@pytest.mark.parametrize("root_count", [0, 1, 2])
def test_cloud_config_installs_only_one_authenticated_root(
    desktop: ModuleType, tmp_path: Path, root_count: int
) -> None:
    """Install the supplied public root before IPA enrollment and reject ambiguity."""
    config = desktop.build_cloud_config(
        "token", "aperture.home.arpa", "openvox.home.arpa", "home.arpa", "ca"
    )
    command = next(
        command
        for command in config["runcmd"]
        if isinstance(command, list) and command[0] == "/opt/puppetlabs/puppet/bin/ruby"
    )
    root = (PROXMOX.parent / "docker/wolf/proxmox-cortex-pve-root-ca.pem").read_text()
    bundle = tmp_path / "ca.pem"
    bundle.write_text(root * root_count)
    installed = tmp_path / "infrastructure.crt"
    result = subprocess.run(
        [*command[:-2], str(bundle), str(installed)], capture_output=True, text=True
    )
    if root_count == 1:
        assert result.returncode == 0, result.stderr
        assert installed.read_text() == root
    else:
        assert result.returncode != 0
        assert not installed.exists()
    install = config["runcmd"].index(command)
    update = config["runcmd"].index(["update-ca-certificates"])
    enroll = next(
        index
        for index, step in enumerate(config["runcmd"])
        if isinstance(step, list) and step[0] == "/usr/local/bin/configure-puppet.sh"
    )
    assert install < update < enroll


def test_hostname_verification_rejects_fqdn_as_short_name(
    desktop: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A guest reporting its FQDN from hostname cannot silently pass deployment."""
    monkeypatch.setattr(
        desktop,
        "run",
        Mock(
            return_value=subprocess.CompletedProcess(
                [], 0, json.dumps({"exitcode": 0, "out-data": "atlas.home.arpa\n"}), ""
            )
        ),
    )
    with pytest.raises(RuntimeError, match="hostname does not equal atlas"):
        desktop.verify_guest_hostname("210", "atlas", "atlas.home.arpa")


@pytest.mark.parametrize(
    "inventory",
    [{}, None, "invalid", [None], [{"vmid": True}], [{"vmid": "210"}], [{"vmid": 0}]],
)
def test_invalid_inventory_fails_before_credentials(
    desktop: ModuleType, monkeypatch: pytest.MonkeyPatch, inventory: object
) -> None:
    """A malformed cluster inventory cannot be interpreted as an available ID."""
    run = Mock(
        return_value=subprocess.CompletedProcess([], 0, json.dumps(inventory), "")
    )
    token = Mock()
    monkeypatch.setattr(desktop, "run", run)
    monkeypatch.setattr(desktop, "create_vault_token", token)
    with pytest.raises(ValueError, match="Invalid cluster VM inventory"):
        desktop.provision(
            desktop.parse_args(["-n", "atlas", "-i", "210"]),
            {"TEMPLATE_ID": "9001", "DOMAIN_SUFFIX": "home.arpa"},
        )
    token.assert_not_called()
    assert run.call_count == 1


def test_hostname_collision_fails_before_credentials(
    desktop: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another VMID cannot reuse the requested hostname and enrollment identity."""
    run = Mock(
        return_value=subprocess.CompletedProcess(
            [], 0, json.dumps([{"vmid": 211, "name": "atlas"}]), ""
        )
    )
    monkeypatch.setattr(desktop, "run", run)
    with pytest.raises(ValueError, match="Hostname atlas is already allocated"):
        desktop.provision(
            desktop.parse_args(["-n", "atlas", "-i", "210"]),
            {"TEMPLATE_ID": "9001", "DOMAIN_SUFFIX": "home.arpa"},
        )
    assert run.call_count == 1


def test_remote_owned_vm_cannot_be_rebuilt(
    desktop: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ownership marker cannot authorize deleting a VM on another node."""
    run = Mock(
        return_value=subprocess.CompletedProcess(
            [],
            0,
            json.dumps(
                [{"vmid": 210, "type": "qemu", "node": "remote", "name": "atlas"}]
            ),
            "",
        )
    )
    monkeypatch.setattr(desktop, "run", run)
    with pytest.raises(ValueError, match="already in use"):
        desktop.provision(
            desktop.parse_args(["-n", "atlas", "-i", "210", "--rebuild"]),
            {"TEMPLATE_ID": "9001", "DOMAIN_SUFFIX": "home.arpa"},
        )
    assert run.call_count == 1


@pytest.mark.parametrize(
    "source",
    [
        None,
        {"vmid": 9001, "type": "lxc", "node": "pve"},
        {"vmid": 9001, "type": "qemu", "node": "remote"},
        {"vmid": 9001, "type": "qemu", "node": "pve"},
    ],
)
def test_source_must_be_local_template(
    desktop: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    source: dict[str, object] | None,
) -> None:
    """A missing source, container, remote guest, or ordinary VM cannot be cloned."""
    run = Mock(
        side_effect=[
            subprocess.CompletedProcess(
                [], 0, json.dumps([source] if source else []), ""
            ),
            subprocess.CompletedProcess([], 0, "name: ordinary-vm\n", ""),
        ]
    )
    token = Mock()
    monkeypatch.setattr(desktop, "run", run)
    monkeypatch.setattr(desktop, "create_vault_token", token)
    with pytest.raises(ValueError, match="Source must be"):
        desktop.provision(
            desktop.parse_args(["-n", "atlas", "-i", "210"]),
            {"TEMPLATE_ID": "9001", "DOMAIN_SUFFIX": "home.arpa"},
        )
    token.assert_not_called()


def test_cloudinit_readiness_times_out(
    desktop: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unavailable guest agent cannot leave provisioning running forever."""
    monotonic = Mock(side_effect=[0, 0, 1800])
    run = Mock(return_value=subprocess.CompletedProcess([], 1, "", "not ready"))
    monkeypatch.setattr(desktop.time, "monotonic", monotonic)
    monkeypatch.setattr(desktop.time, "sleep", lambda _: None)
    monkeypatch.setattr(desktop, "run", run)
    with pytest.raises(RuntimeError, match="Timed out"):
        desktop.wait_for_cloudinit("210")
    assert run.call_count == 1


@pytest.mark.parametrize("encoded", [False, True])
def test_owned_vm_accepts_proxmox_encoded_description(
    desktop: ModuleType, monkeypatch: pytest.MonkeyPatch, encoded: bool
) -> None:
    """Decode qm config's URI escaping once before exact ownership comparison."""
    marker = "openvox-provisioning:desktop:atlas.home.arpa"
    description = quote(marker, safe="") if encoded else marker
    run = Mock(
        return_value=subprocess.CompletedProcess(
            [], 0, f"name: atlas\ndescription: {description}\n", ""
        )
    )
    monkeypatch.setattr(desktop, "run", run)
    desktop.require_owned_vm("210", "atlas", marker)
    run.assert_called_once_with("qm", "config", "210")
