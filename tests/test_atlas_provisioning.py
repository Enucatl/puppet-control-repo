"""Exercise Atlas provisioning safety and guest identity without real VMs."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote

import pytest

from test_puppet_provisioning import parse_cloud_template


PROXMOX = Path(__file__).resolve().parents[1] / "proxmox"
OWNER = "openvox-provisioning:docker:atlas.home.arpa"
CONFIG = f"name: atlas\ndescription: {OWNER}\n"
VM = {"vmid": 210, "name": "atlas", "type": "qemu", "node": "pve"}


def target_check(
    tmp_path: Path,
    resources: list[dict] | str,
    *,
    config: str = CONFIG,
    rebuild: bool = False,
    vmid: str = "210",
    remove: bool = False,
    second_config: str | None = None,
    resource_status: int = 0,
    config_status: int = 0,
    template: bool = False,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run the shared preflight with recording Proxmox shell functions."""
    log = tmp_path / "commands"
    script = r"""
set -euo pipefail
source "$1"
TEMPLATE_ID=9001
hostname() { printf 'pve\n'; }
pvesh() {
    printf 'pvesh %s\n' "$*" >> "$COMMAND_LOG"
    printf '%s\n' "$RESOURCES"
    return "$RESOURCE_STATUS"
}
qm() {
    printf 'qm %s\n' "$*" >> "$COMMAND_LOG"
    if [ "$1" = config ]; then
        if [ -f "$CONFIG_CALLED" ]; then
            printf '%s' "$SECOND_CONFIG"
        else
            touch "$CONFIG_CALLED"
            printf '%s' "$CONFIG"
        fi
        return "$CONFIG_STATUS"
    fi
}
validate_target "$TARGET_ID" "$TARGET_NAME" "$TARGET_OWNER" "$REBUILD"
if [ "$REMOVE" = 1 ]; then remove_target "$TARGET_ID"; fi
"""
    result = subprocess.run(
        ["bash", "-c", script, "test", str(PROXMOX / "lib.sh")],
        env={
            **os.environ,
            "COMMAND_LOG": str(log),
            "CONFIG_CALLED": str(tmp_path / "config-called"),
            "RESOURCES": resources
            if isinstance(resources, str)
            else json.dumps(resources),
            "CONFIG": config,
            "SECOND_CONFIG": config if second_config is None else second_config,
            "CONFIG_STATUS": str(config_status),
            "RESOURCE_STATUS": str(resource_status),
            "TARGET_ID": vmid,
            "TARGET_NAME": "ubuntu-server-2604-template" if template else "atlas",
            "TARGET_OWNER": (
                "openvox-provisioning:template:ubuntu-26.04" if template else OWNER
            ),
            "TARGET_IS_TEMPLATE": "1" if template else "0",
            "REBUILD": str(rebuild).lower(),
            "REMOVE": "1" if remove else "0",
        },
        capture_output=True,
        text=True,
    )
    return result, log.read_text().splitlines() if log.exists() else []


@pytest.mark.parametrize("rebuild", [False, True])
def test_unused_target_never_requires_or_destroys_a_vm(
    tmp_path: Path, rebuild: bool
) -> None:
    """Allow a free Atlas ID without issuing mutation commands."""
    result, calls = target_check(tmp_path, [], rebuild=rebuild)
    assert result.returncode == 0, result.stderr
    assert not any(call.startswith(("qm stop", "qm destroy")) for call in calls)


@pytest.mark.parametrize("encoded", [False, True])
@pytest.mark.parametrize("rebuild", [False, True])
def test_owned_target_requires_explicit_rebuild(
    tmp_path: Path, rebuild: bool, encoded: bool
) -> None:
    """Only remove an existing Atlas after an explicit ownership-checked rebuild."""
    config = CONFIG.replace(OWNER, quote(OWNER, safe="")) if encoded else CONFIG
    result, calls = target_check(
        tmp_path, [VM], config=config, rebuild=rebuild, remove=True
    )
    assert (result.returncode == 0) == rebuild, result.stderr
    mutations = [call for call in calls if call.startswith(("qm stop", "qm destroy"))]
    assert mutations == (["qm stop 210", "qm destroy 210 --purge"] if rebuild else [])


@pytest.mark.parametrize(
    ("resources", "config"),
    [
        (
            [{**VM, "name": "someone-else"}],
            CONFIG.replace("name: atlas", "name: someone-else"),
        ),
        ([VM], CONFIG.replace(OWNER, "unmanaged")),
        ([VM], CONFIG.replace(OWNER, quote(OWNER + "-tampered", safe=""))),
        ([VM], CONFIG.replace(OWNER, quote(quote(OWNER, safe=""), safe=""))),
        ([VM], CONFIG + "template: 1\n"),
        ([{**VM, "type": "lxc"}], CONFIG),
        ([{**VM, "vmid": 211}], CONFIG),
        ([{**VM, "vmid": 211, "type": "lxc"}], CONFIG),
    ],
)
def test_rebuild_refuses_unrelated_or_ambiguous_target(
    tmp_path: Path, resources: list[dict], config: str
) -> None:
    """Preserve unmanaged guests, containers, templates and duplicate names."""
    result, calls = target_check(
        tmp_path, resources, config=config, rebuild=True, remove=True
    )
    assert result.returncode != 0
    assert not any(call.startswith(("qm stop", "qm destroy")) for call in calls)


@pytest.mark.parametrize("vmid", ["9000", "9001", "0", "--help", "abc"])
def test_regular_guest_cannot_target_template_or_invalid_id(
    tmp_path: Path, vmid: str
) -> None:
    """Protect both the old and replacement template IDs before VM commands."""
    result, calls = target_check(tmp_path, [], vmid=vmid, rebuild=True, remove=True)
    assert result.returncode != 0
    assert not any(call.startswith("qm ") for call in calls)


def test_template_builder_can_create_its_reserved_id(tmp_path: Path) -> None:
    """Allow the template builder's explicit exemption for the new template ID."""
    result, _ = target_check(tmp_path, [], vmid="9001", template=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("encoded", [False, True])
def test_template_builder_can_rebuild_encoded_owned_template(
    tmp_path: Path, encoded: bool
) -> None:
    """Accept the exact template marker in both decoded and qm CLI encoded form."""
    owner = "openvox-provisioning:template:ubuntu-26.04"
    description = quote(owner, safe="") if encoded else owner
    config = (
        f"name: ubuntu-server-2604-template\ndescription: {description}\ntemplate: 1\n"
    )
    vm = {
        "vmid": 9001,
        "name": "ubuntu-server-2604-template",
        "type": "qemu",
        "node": "pve",
    }
    result, calls = target_check(
        tmp_path,
        [vm],
        config=config,
        rebuild=True,
        remove=True,
        vmid="9001",
        template=True,
    )
    assert result.returncode == 0, result.stderr
    assert "qm destroy 9001 --purge" in calls


@pytest.mark.parametrize(
    ("resources", "resource_status", "config_status"),
    [("not-json", 0, 0), ("{}", 0, 0), ("[]", 1, 0), (json.dumps([VM]), 0, 1)],
)
def test_preflight_fails_closed_when_inventory_cannot_be_verified(
    tmp_path: Path, resources: str, resource_status: int, config_status: int
) -> None:
    """Never interpret an inventory or configuration failure as a free VM ID."""
    result, calls = target_check(
        tmp_path,
        resources,
        rebuild=True,
        remove=True,
        resource_status=resource_status,
        config_status=config_status,
    )
    assert result.returncode != 0
    assert not any(call.startswith(("qm stop", "qm destroy")) for call in calls)


def test_removal_rechecks_owner_after_preflight(tmp_path: Path) -> None:
    """Do not destroy a target whose owner changed after preflight."""
    result, calls = target_check(
        tmp_path,
        [VM],
        rebuild=True,
        remove=True,
        second_config=CONFIG.replace(OWNER, "new-owner"),
    )
    assert result.returncode != 0
    assert not any(call.startswith(("qm stop", "qm destroy")) for call in calls)


@pytest.mark.parametrize(
    ("hostname", "fqdn", "exitcode", "valid"),
    [
        ("atlas", "atlas.home.arpa", 0, True),
        ("atlas.home.arpa", "atlas.home.arpa", 0, False),
        ("atlas", "atlas", 0, False),
        ("atlas", "atlas.home.arpa", 1, False),
    ],
)
def test_guest_identity_requires_short_hostname_and_successful_fqdn(
    hostname: str, fqdn: str, exitcode: int, valid: bool
) -> None:
    """Reject incorrect names and failed guest commands during deployment checks."""
    script = r"""
set -euo pipefail
source "$1"
qm() {
    if [[ "$*" == *'hostname -f' ]]; then printf '%s' "$FQDN_RESULT"
    else printf '%s' "$HOSTNAME_RESULT"; fi
}
verify_guest_identity 210 atlas atlas.home.arpa
"""
    result = subprocess.run(
        ["bash", "-c", script, "test", str(PROXMOX / "lib.sh")],
        env={
            **os.environ,
            "HOSTNAME_RESULT": json.dumps({"exitcode": exitcode, "out-data": hostname}),
            "FQDN_RESULT": json.dumps({"exitcode": exitcode, "out-data": fqdn}),
        },
        capture_output=True,
        text=True,
    )
    assert (result.returncode == 0) == valid, result.stderr


@pytest.mark.parametrize("failure", ["", "root", "ssh", "bridge", "snippets", "images"])
def test_host_preflight_checks_required_storage_and_access(
    tmp_path: Path, failure: str
) -> None:
    """Validate prerequisites without requiring root, Proxmox or host directories."""
    key = tmp_path / "id_ed25519.pub"
    if failure != "ssh":
        key.write_text("ssh-ed25519 fixture\n")
    snippets = tmp_path / "snippets"
    snippets.mkdir()
    iso = tmp_path / "iso"
    iso.mkdir()
    script = (PROXMOX / "lib.sh").read_text()
    script = script.replace("~/.ssh/id_ed25519.pub", str(key))
    script = script.replace("/var/lib/vz/snippets", str(snippets))
    script = script.replace("/var/lib/vz/template/iso", str(iso))
    script += r"""
set -euo pipefail
SNIPPET_STORAGE=snippets
DEFAULT_STORAGE=local-zfs
id() { if [ "$FAILURE" = root ]; then echo 1000; else echo 0; fi; }
ip() { [ "$FAILURE" != bridge ]; }
qm() { :; }
pvesh() {
    if [ "$2" = /storage/snippets ]; then
        if [ "$FAILURE" = snippets ]; then echo '{"path":"/wrong","content":"snippets"}'
        else echo '{"path":"/var/lib/vz","content":"iso,snippets"}'; fi
    else
        if [ "$FAILURE" = images ]; then echo '{"content":"rootdir"}'
        else echo '{"content":"images,rootdir"}'; fi
    fi
}
check_provisioning_host
"""
    result = subprocess.run(
        ["bash", "-c", script],
        env={**os.environ, "FAILURE": failure},
        capture_output=True,
        text=True,
    )
    assert (result.returncode == 0) == (failure == ""), result.stderr


@pytest.mark.parametrize(
    "failure", ["", "boot-query", "reboot", "no-reboot", "cloud-init", "identity"]
)
def test_reboot_waits_for_new_boot_then_checks_cloudinit_and_identity(
    tmp_path: Path, failure: str
) -> None:
    """Require a changed boot ID and propagate failures without a real reboot."""
    script = r"""
set -euo pipefail
source "$1"
guest_exec() {
    if [ "$FAILURE" = boot-query ]; then return 1; fi
    if [ -f "$REBOOTED" ] && [ "$FAILURE" != no-reboot ]; then echo new-boot
    else echo original-boot; fi
}
qm() {
    printf 'qm %s\n' "$*" >> "$COMMAND_LOG"
    [ "$FAILURE" != reboot ] || return 1
    touch "$REBOOTED"
}
sleep() { SECONDS=$((SECONDS + 301)); }
wait_for_cloudinit() {
    echo cloud-init >> "$COMMAND_LOG"
    [ "$FAILURE" != cloud-init ]
}
verify_guest_identity() {
    printf 'identity %s\n' "$*" >> "$COMMAND_LOG"
    [ "$FAILURE" != identity ]
}
reboot_and_verify_guest 210 atlas atlas.home.arpa
"""
    log = tmp_path / "commands"
    result = subprocess.run(
        ["bash", "-c", script, "test", str(PROXMOX / "lib.sh")],
        env={
            **os.environ,
            "FAILURE": failure,
            "REBOOTED": str(tmp_path / "rebooted"),
            "COMMAND_LOG": str(log),
        },
        capture_output=True,
        text=True,
        timeout=5,
    )
    calls = log.read_text().splitlines() if log.exists() else []
    assert (result.returncode == 0) == (failure == ""), result.stderr
    if failure == "boot-query":
        assert calls == []
    elif failure in ("reboot", "no-reboot"):
        assert calls == ["qm reboot 210"]
    elif failure == "cloud-init":
        assert calls == ["qm reboot 210", "cloud-init"]
    else:
        assert calls == [
            "qm reboot 210",
            "cloud-init",
            "identity 210 atlas atlas.home.arpa",
        ]
    if failure == "no-reboot":
        assert "Timed out waiting for the guest reboot" in result.stderr


def run_atlas(
    tmp_path: Path,
    *,
    failure: str = "",
    resources: list[dict] | None = None,
    arguments: tuple[str, ...] = (),
    script_name: str = "atlas-server.sh",
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run copied deployment scripts with harmless recording Proxmox binaries."""
    scripts = tmp_path / "scripts"
    shutil.copytree(PROXMOX, scripts)
    snippets = tmp_path / "snippets"
    snippets.mkdir()
    for script in scripts.glob("*.sh"):
        script.write_text(
            script.read_text().replace("/var/lib/vz/snippets", str(snippets))
        )
    with (scripts / "lib.sh").open("a") as library:
        library.write(
            "\nload_env() { :; }\n"
            "check_provisioning_host() { :; }\n"
            'create_vault_token() { echo unexpected-token >> "$COMMAND_LOG"; return 99; }\n'
        )
    bins = tmp_path / "bin"
    bins.mkdir()
    commands = {
        "hostname": "printf 'pve\\n'",
        "curl": "exit 99",
        "pvesh": 'if [ -f "$MOCK_CLONED" ]; then printf "%s\\n" "$CLONED_RESOURCE"; else printf "%s\\n" "$RESOURCES"; fi',
        "qm": r"""
case "$1" in
    clone)
        cp "$SNIPPET_DIR"/*.yml "$RENDERED"
        if [ "$FAILURE" = clone ]; then exit 42; fi
        touch "$MOCK_CLONED"
        ;;
    config)
        if [ "$2" = 9001 ]; then printf 'template: 1\n'; exit 0; fi
        [ -f "$MOCK_CLONED" ] || exit 1
        printf 'name: atlas\ndescription: openvox-provisioning:docker:atlas.home.arpa\n'
        if [ "$FAILURE" != net0 ]; then
            printf 'net0: virtio=DE:AD:BE:EF:00:01,bridge=vmbr0,firewall=1,tag=12,mtu=1450\n'
        fi
        ;;
    set)
        if [ "$FAILURE" = set ] && [[ "$*" == *--cores* ]]; then exit 42; fi
        ;;
    destroy) rm -f "$MOCK_CLONED" ;;
    reboot) touch "$MOCK_REBOOTED" ;;
    guest)
        if [[ "$*" == *'cloud-init status'* ]]; then output='status: done'
        elif [[ "$*" == *'/proc/sys/kernel/random/boot_id'* ]]; then
            if [ -f "$MOCK_REBOOTED" ]; then output=new-boot; else output=original-boot; fi
        elif [[ "$*" == *'hostname -f'* ]]; then output=atlas.home.arpa
        elif [[ "$*" == *'hostname'* ]]; then output=atlas
        else output='guest output'; fi
        jq -cn --arg output "$output" '{exitcode: 0, "out-data": $output}'
        ;;
esac
""",
    }
    for name, body in commands.items():
        command = bins / name
        command.write_text(
            '#!/bin/bash\nprintf "%s %s\\n" "${0##*/}" "$*" >> "$COMMAND_LOG"\n'
            + body
            + "\n"
        )
        command.chmod(0o755)
    result = subprocess.run(
        ["bash", str(scripts / script_name), *arguments],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{bins}:{os.environ['PATH']}",
            "COMMAND_LOG": str(tmp_path / "commands"),
            "SNIPPET_DIR": str(snippets),
            "RENDERED": str(tmp_path / "rendered.yml"),
            "MOCK_CLONED": str(tmp_path / "cloned"),
            "MOCK_REBOOTED": str(tmp_path / "rebooted"),
            "RESOURCES": json.dumps(resources or []),
            "CLONED_RESOURCE": json.dumps([VM]),
            "FAILURE": failure,
            "IPV6_PREFIX": "fd00:1234:5678",
            "LOG_DIR": str(tmp_path / "logs"),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    log = tmp_path / "commands"
    return result, log.read_text().splitlines() if log.exists() else []


@pytest.mark.parametrize("failure", ["", "clone", "set", "net0"])
def test_atlas_base_deployment_and_owned_failure_cleanup(
    tmp_path: Path, failure: str
) -> None:
    """Deploy the requested Atlas shape and clean only successfully claimed clones."""
    result, calls = run_atlas(tmp_path, failure=failure)
    assert (result.returncode == 0) == (failure == ""), result.stderr
    assert "unexpected-token" not in calls
    assert not any(call.startswith("curl ") for call in calls)
    clone = next(call for call in calls if call.startswith("qm clone "))
    assert clone.startswith("qm clone 9001 210 --name atlas ")
    if failure == "":
        assert any("--searchdomain home.arpa" in call for call in calls)
    destroys = [call for call in calls if call.startswith("qm destroy ")]
    assert destroys == (
        ["qm destroy 210 --purge"] if failure in ("set", "net0") else []
    )
    if failure not in ("clone", "net0"):
        settings = next(
            call
            for call in calls
            if call.startswith("qm set 210 ") and "--cores" in call
        )
        assert "--memory 12288" in settings
    if not failure:
        network = "qm set 210 --net0 virtio=bc:24:11:4e:a4:9e,bridge=vmbr0,firewall=1,tag=12,mtu=1450"
        assert network in calls
        assert calls.index(network) < calls.index("qm start 210")
        assert "qm resize 210 scsi0 300G" in calls
        assert calls.count("qm reboot 210") == 1
        assert calls.count("qm guest exec 210 -- hostname") == 2
        assert calls.count("qm guest exec 210 -- hostname -f") == 2
    assert list((tmp_path / "snippets").iterdir()) == []
    config = parse_cloud_template((tmp_path / "rendered.yml").read_text())
    assert config["hostname"] == "atlas"
    assert config["fqdn"] == "atlas.home.arpa"
    assert config["prefer_fqdn_over_hostname"] is False
    assert config["manage_etc_hosts"] is True
    assert "configure-puppet" not in json.dumps(config)
    files = {item["path"]: item["content"] for item in config["write_files"]}
    assert (
        "hostname: atlas\nfqdn: atlas.home.arpa\n"
        in files["/etc/cloud/cloud.cfg.d/99-hostname.cfg"]
    )
    scratch = files["/etc/tmpfiles.d/atlas-scratch.conf"]
    assert "d /scratch 0755 root root -" in scratch
    assert "d /scratch/apt-cacher-ng 0755 100101 100101 2d" in scratch
    assert "d /scratch/backup/vault 0700 root root 7d" in scratch
    assert "systemctl enable --now systemd-tmpfiles-clean.timer" in config["runcmd"]
    assert not config.get("mounts")


@pytest.mark.parametrize(
    "mac",
    ["", "invalid", "00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff", "01:24:11:4e:a4:9e"],
)
def test_atlas_rejects_invalid_or_multicast_mac_before_mutations(
    tmp_path: Path, mac: str
) -> None:
    """Reject unusable reservation addresses before creating a VM or snippet."""
    result, calls = run_atlas(tmp_path, arguments=("-a", mac))
    assert result.returncode != 0
    assert "nonzero unicast MAC address" in result.stderr
    assert calls == []
    assert list((tmp_path / "snippets").iterdir()) == []


def test_atlas_mac_can_be_overridden(tmp_path: Path) -> None:
    """Allow an explicit replacement reservation without losing NIC options."""
    result, calls = run_atlas(tmp_path, arguments=("-a", "02:AA:BB:CC:DD:EE"))
    assert result.returncode == 0, result.stderr
    assert (
        "qm set 210 --net0 virtio=02:AA:BB:CC:DD:EE,bridge=vmbr0,firewall=1,tag=12,mtu=1450"
        in calls
    )


def test_shared_docker_creation_keeps_generated_mac_without_option(
    tmp_path: Path,
) -> None:
    """Preserve ordinary Docker provisioning's generated MAC behavior."""
    result, calls = run_atlas(
        tmp_path,
        arguments=("-b", "-n", "atlas", "-i", "210"),
        script_name="docker-server.sh",
    )
    assert result.returncode == 0, result.stderr
    assert not any("--net0" in call for call in calls)


def test_atlas_preflight_precedes_token_snippet_or_mutations(tmp_path: Path) -> None:
    """Refuse occupied VMID 210 before creating a snippet or touching the guest."""
    result, calls = run_atlas(tmp_path, resources=[VM])
    assert result.returncode != 0
    assert not (tmp_path / "rendered.yml").exists()
    assert list((tmp_path / "snippets").iterdir()) == []
    assert "unexpected-token" not in calls
    assert not any(
        call.startswith(("qm clone ", "qm set ", "qm stop ", "qm destroy "))
        for call in calls
    )
