"""Check the disposable XFS builder's disk and identity safety boundaries."""

import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_curtin_targets_only_serial_selected_blank_disk() -> None:
    """Keep installer writes off the builder root and production disks."""
    config = yaml.safe_load((ROOT / "proxmox/ubuntu-xfs-curtin.yml").read_text())
    actions = config["storage"]["config"]
    disks = [action for action in actions if action["type"] == "disk"]
    assert disks == [
        {
            "id": "target",
            "type": "disk",
            "path": "__TARGET_DISK__",
            "ptable": "gpt",
            "wipe": "superblock",
            "grub_device": True,
        }
    ]
    formats = {action["id"]: action for action in actions if action["type"] == "format"}
    assert formats["root-fs"]["fstype"] == "xfs"
    assert formats["root-fs"]["extra_options"] == ["-m", "reflink=1", "-n", "ftype=1"]
    assert formats["esp-fs"]["fstype"] == "fat32"
    assert formats["boot-fs"]["fstype"] == "ext4"
    mounts = {action["path"] for action in actions if action["type"] == "mount"}
    assert mounts == {"/", "/boot", "/boot/efi"}
    root_mount = next(action for action in actions if action["id"] == "root-mount")
    assert root_mount["options"] == "noatime"
    assert config["kernel"]["package"] == "linux-generic"
    assert config["swap"]["size"] == 0


def test_template_rejects_conflicting_ids_before_proxmox() -> None:
    """Reject ID collisions before any provisioning operation."""
    for args in (["-i", "9001"], ["-b", "9001"], ["-b", "9002"]):
        result = subprocess.run(
            ["bash", str(ROOT / "proxmox/ubuntu-xfs-template.sh"), *args],
            cwd=ROOT / "tests",
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "IDs must differ" in result.stderr


def test_target_boot_uses_fresh_vm_and_firmware() -> None:
    """Validate the installed target without reusing builder disks or NVRAM."""
    script = (ROOT / "proxmox/ubuntu-xfs-template.sh").read_text()
    assert (
        'qm disk move "$BUILDER_ID" scsi1 --target-vmid "$XFS_TEMPLATE_ID" --target-disk scsi0'
        in script
    )
    assert (
        script.count('--efidisk0 "$DEFAULT_STORAGE:0,efitype=4m,pre-enrolled-keys=0"')
        == 2
    )
    assert "qm destroy" not in script
    assert "test -d /sys/firmware/efi" in script
    assert "blockdev --getsize64" in script
    assert "lsblk -nr -o MOUNTPOINTS" in script
    assert "virtio-scsi-pci" not in script
    assert "--scsihw virtio-scsi-single" in script
    assert "discard=on,ssd=1,iothread=1" in script
    assert "'$ROOT_SHA256' /var/tmp/ubuntu-root.tar.xz | sha256sum --check -" in script


def test_generated_cloud_config_and_disk_guards(tmp_path: Path) -> None:
    """Render real builder YAML and reject missing or ambiguous serials."""
    import os

    script = (ROOT / "proxmox/ubuntu-xfs-template.sh").read_text()
    start = script.index("{\n    cat <<YAML")
    end = script.index('} > "$SNIPPET_PATH"', start) + len('} > "$SNIPPET_PATH"')
    snippet = tmp_path / "builder.yml"
    result = subprocess.run(
        ["bash", "-eu", "-c", script[start:end]],
        env={
            **os.environ,
            "SCRIPT_DIR": str(ROOT / "proxmox"),
            "SNIPPET_PATH": str(snippet),
            "SSH_PUBLIC_KEY_JSON": '"ssh-ed25519 example"',
            "RELEASE_URL": "https://example.invalid",
            "ROOT_NAME": "root.tar.xz",
            "ROOT_SHA256": "root-checksum",
            "CURTIN_REVISION": "source-revision",
            "CURTIN_SHA256": "source-checksum",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    config = yaml.safe_load(snippet.read_text())
    command = config["runcmd"][1]
    assert "curtin" not in config["packages"]
    assert "source-checksum" in command
    assert "file:///var/tmp/ubuntu-root.tar.xz" in config["write_files"][0]["content"]
    assert subprocess.run(["bash", "-n"], input=command, text=True).returncode == 0
    guard = command[: command.index("curl --fail")]
    mocks = r"""
lsblk() {
    case "$*" in
        '-dn -o PATH,SERIAL') printf '%s\n' "$DISKS" ;;
        '-dn -o SERIAL /dev/sdb') echo openvox-xfs-target ;;
        '-nr -o NAME /dev/sdb') echo sdb ;;
        '-nr -o MOUNTPOINTS /dev/sdb') echo '' ;;
        '-no PKNAME /dev/sda1') echo sda ;;
        *) return 1 ;;
    esac
}
blockdev() { echo 12884901888; }
findmnt() { echo /dev/sda1; }
test() {
    if [ "$1" = -b ] || [ "$1" = -d ]; then return 0; fi
    builtin test "$@"
}
"""
    for disks, expected in (
        ("/dev/sdb openvox-xfs-target", 0),
        ("/dev/sdb other-target", 1),
        ("/dev/sdb openvox-xfs-target\n/dev/sdc openvox-xfs-target", 1),
    ):
        checked = subprocess.run(
            ["bash", "-ec", mocks + guard],
            env={**os.environ, "DISKS": disks},
            capture_output=True,
            text=True,
        )
        assert checked.returncode == expected, checked.stderr
