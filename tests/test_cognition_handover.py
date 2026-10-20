"""Exercise migration guards and failed GPU assignment recovery without live VMs."""

import importlib.util
from pathlib import Path
import subprocess

import pytest


def load(name):
    """Load the standalone migration tool under test."""
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).parents[1] / "proxmox" / f"{name.replace('_', '-')}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("running", ("200", "220"))
def test_gpu_refuses_either_running_guest(tmp_path, monkeypatch, running):
    """An active guest must never have its PCI assignments changed."""
    module = load("cognition_gpu_handover")
    commands = []

    def qm(*args):
        commands.append(args)
        if args[0] == "config":
            return "name: " + {"200": "complex", "220": "cognition"}[args[1]]
        return "status: " + ("running" if args[1] == running else "stopped")

    monkeypatch.setattr(module, "qm", qm)
    with pytest.raises(ValueError, match="must be stopped"):
        module.move("200", "220", tmp_path)
    assert not any(command[0] == "set" for command in commands)


def test_gpu_restores_source_if_target_assignment_fails(tmp_path, monkeypatch):
    """A failed target assignment restores the retained source PCI configuration."""
    module = load("cognition_gpu_handover")
    commands = []

    def qm(*args):
        commands.append(args)
        if args[0] == "config":
            return {
                "200": "name: complex\nhostpci0: 0000:01:00,pcie=1",
                "220": "name: cognition\nmachine: q35\nbios: ovmf",
            }[args[1]]
        if args[0] == "status":
            return "status: stopped"
        if args[:2] == ("set", "220"):
            raise subprocess.CalledProcessError(1, args)
        return ""

    monkeypatch.setattr(module, "qm", qm)
    with pytest.raises(subprocess.CalledProcessError):
        module.move("200", "220", tmp_path)
    assert commands[-1] == ("set", "200", "--hostpci0", "0000:01:00,pcie=1")
    assert (tmp_path / "gpu-200-to-220.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("target_active", (False, True))
def test_copy_refuses_active_writers(monkeypatch, target_active):
    """Final sync cannot copy over active containers on either guest."""
    module = load("cognition_copy_state")
    monkeypatch.setattr(
        module.subprocess,
        "check_output",
        lambda *a, **kw: "id" if target_active else "",
    )
    monkeypatch.setattr(module, "remote", lambda *args: "id")
    with pytest.raises(ValueError, match="Stop all"):
        module.check_idle(final=True)


def test_sync_refuses_symlink_destination(tmp_path):
    """A redirected destination cannot let rsync delete another tree."""
    module = load("cognition_copy_state")
    destination = tmp_path / "destination"
    destination.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        module.sync("/etc/wolf", destination, final=True)


def test_copy_refuses_different_user_namespace_modes(monkeypatch):
    """Numerical volume ownership must not silently cross incompatible daemons."""
    module = load("cognition_copy_state")
    monkeypatch.setattr(module, "remote", lambda *args: '["name=userns"]')
    monkeypatch.setattr(
        module.subprocess,
        "check_output",
        lambda command, **kw: "" if command[1] == "ps" else "[]",
    )
    with pytest.raises(ValueError, match="user namespaces differ"):
        module.check_idle(final=False)


def test_copy_ignores_dormant_uid_mappings(monkeypatch):
    """Disabled user namespaces do not depend on historical dockremap accounts."""
    module = load("cognition_copy_state")
    remote_commands = []

    def remote(*args):
        remote_commands.append(args)
        return "[]"

    monkeypatch.setattr(module, "remote", remote)
    monkeypatch.setattr(
        module.subprocess,
        "check_output",
        lambda command, **kw: "" if command[1] == "ps" else "[]",
    )
    module.check_idle(final=False)
    assert all(command[0] != "cat" for command in remote_commands)


def test_remote_reads_use_operator_sudo(monkeypatch):
    """Source operations work when direct root SSH is disabled."""
    module = load("cognition_copy_state")
    commands = []
    monkeypatch.setattr(
        module.subprocess,
        "check_output",
        lambda command, **kw: commands.append(command) or "",
    )
    module.remote("cat", "/etc/subuid")
    assert "user_l@complex.home.arpa" in commands[0]
    assert "-oStrictHostKeyChecking=yes" in commands[0]
    assert commands[0][-1] == "sudo -n cat /etc/subuid"


def test_home_copy_preserves_new_host_identity(tmp_path, monkeypatch):
    """A final home copy cannot replace Cognition's issued TLS identity."""
    module = load("cognition_copy_state")
    commands = []
    monkeypatch.setattr(
        module.subprocess, "run", lambda command, **kw: commands.append(command)
    )
    module.sync("/home", tmp_path / "home", final=True)
    assert "--exclude=/*/.config/home-arpa/mtls/" in commands[0]
    assert "--exclude=/user_l/.ssh/" in commands[0]
    assert "--delete-excluded" not in commands[0]
    assert "--rsync-path=sudo -n rsync" in commands[0]
    assert "user_l@complex.home.arpa:/home/" in commands[0]


def test_vllm_wrapper_passes_cognition_target(tmp_path):
    """The compatibility profile passes the new guest through to the service."""
    from puppet_catalog import DEPENDENCIES, compile_catalog, resources

    if not (DEPENDENCIES / "systemd/manifests/unit_file.pp").is_file():
        pytest.skip("Set PUPPET_TEST_MODULEPATH to the installed Forge dependencies")

    catalog = compile_catalog(
        tmp_path,
        "service { 'alloy': } class { 'profile::vllm_weekly_window': vm_host => 'cognition.home.arpa', vm_id => 220 }",
        hostname="proxmox",
        node_type="proxmox",
    )
    service = resources(catalog)[
        "File[/etc/systemd/system/vllm-weekly-window.service]"
    ]["content"]
    assert "--vm-host cognition.home.arpa --vm-id 220" in service
