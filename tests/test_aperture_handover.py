"""Exercise the stopped desktop transfer and partial-failure rollback."""

import importlib.util
from pathlib import Path
import subprocess

import pytest


@pytest.fixture
def handover(monkeypatch):
    """Provide isolated VM configurations and checked command mutations."""
    spec = importlib.util.spec_from_file_location(
        "aperture_handover", Path(__file__).parents[1] / "proxmox/aperture-handover.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    configs = {
        vmid: {
            "name": name,
            "smbios1": uuid,
            "bios": "ovmf",
            "machine": "q35",
            "memory": "16384" if vmid == "300" else "8192",
            "vga": "none" if vmid == "300" else "virtio",
            "net0": f"preserved-{vmid}",
            "scsi0": f"preserved-disk-{vmid}",
        }
        for vmid, (name, uuid) in module.IDENTITIES.items()
    }
    configs["300"].update(module.PCI, onboot="1", startup="order=3")
    running = set()
    calls = []

    def qm(*args):
        calls.append(args)
        command, vmid, *options = args
        if command == "status":
            return "status: " + ("running" if vmid in running else "stopped")
        if command == "config":
            return "\n".join(f"{key}: {value}" for key, value in configs[vmid].items())
        assert command == "set"
        assert not running
        for key, value in zip(options[::2], options[1::2], strict=True):
            if key == "--delete":
                for item in value.split(","):
                    configs[vmid].pop(item)
            else:
                configs[vmid][key.removeprefix("--")] = value
        assert not (module.pci(configs["300"]) and module.pci(configs["310"]))
        return ""

    monkeypatch.setattr(module, "qm", qm)
    monkeypatch.setattr(module, "config", lambda vmid: configs[vmid].copy())
    monkeypatch.setattr(module.Path, "glob", lambda *args: [])
    return module, configs, running, calls


def test_transfer_and_exact_rollback(handover, tmp_path):
    """Transfer every option and restore absent settings and original identities."""
    module, configs, _, _ = handover
    original = {vmid: values.copy() for vmid, values in configs.items()}
    module.handover("prepare", tmp_path)
    module.handover("apply", tmp_path)
    assert not module.pci(configs["300"])
    assert module.pci(configs["310"]) == module.PCI
    assert configs["300"]["memory"] == "4096"
    assert configs["300"]["onboot"] == "0"
    assert configs["310"]["memory"] == "16384"
    assert configs["310"]["onboot"] == "1"
    assert configs["310"]["vga"] == "none"
    assert configs["310"]["startup"] == "order=3"
    module.handover("rollback", tmp_path)
    assert configs == original
    assert (tmp_path / "before.json").stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        module.handover("prepare", tmp_path)


@pytest.mark.parametrize("vmid", ["300", "310"])
def test_running_guest_blocks_transfer(handover, tmp_path, vmid):
    """Neither running guest can have configuration or PCI ownership changed."""
    module, _, running, calls = handover
    module.handover("prepare", tmp_path)
    running.add(vmid)
    with pytest.raises(ValueError, match="must be stopped"):
        module.handover("apply", tmp_path)
    assert not any(call[0] == "set" for call in calls)


def test_wrong_identity_blocks_prepare(handover, tmp_path):
    """A reused VM ID must never pass the identity guard."""
    module, configs, _, _ = handover
    configs["310"]["smbios1"] = "different"
    with pytest.raises(ValueError, match="Unexpected identity"):
        module.handover("prepare", tmp_path)


def test_partial_apply_can_roll_back(handover, tmp_path, monkeypatch):
    """A failed target assignment retains a usable original-state recovery."""
    module, configs, _, _ = handover
    original = {vmid: values.copy() for vmid, values in configs.items()}
    module.handover("prepare", tmp_path)
    qm = module.qm

    def failed_target(*args):
        if args[:2] == ("set", "310"):
            raise subprocess.CalledProcessError(1, args)
        return qm(*args)

    monkeypatch.setattr(module, "qm", failed_target)
    with pytest.raises(subprocess.CalledProcessError):
        module.handover("apply", tmp_path)
    assert not module.pci(configs["300"])
    monkeypatch.setattr(module, "qm", qm)
    module.handover("rollback", tmp_path)
    assert configs == original
