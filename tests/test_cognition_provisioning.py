"""Check Cognition staging resources and authenticated infrastructure trust."""

import shlex
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from test_atlas_provisioning import run_atlas
from test_puppet_provisioning import parse_cloud_template, render_cloud_template

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("remap", ["default", ""])
def test_docker_ownership_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, remap: str
) -> None:
    """Keep new guest container ownership compatible with the source daemon."""
    monkeypatch.setenv("DOCKER_USERNS_REMAP", remap)
    config = parse_cloud_template(render_cloud_template(tmp_path, "docker"))
    daemon = next(
        file["content"]
        for file in config["write_files"]
        if file["path"] == "/etc/docker/daemon.json"
    )
    assert json.loads(daemon)["userns-remap"] == remap


def test_cognition_resources(tmp_path: Path) -> None:
    """Use a distinct VM identity with requested resources and virtual graphics."""
    launcher = tmp_path / "cognition-server.sh"
    shutil.copy(ROOT / "proxmox/cognition-server.sh", launcher)
    (tmp_path / "docker-server.sh").write_text(
        'test "${DOCKER_USERNS_REMAP-unset}" = "" || exit 1\nprintf \'%s\\n\' "$@"\n'
    )
    result = subprocess.run(["bash", str(launcher)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    args = result.stdout.splitlines()
    options = dict(zip(args[::2], args[1::2], strict=True))
    assert options == {
        "-n": "cognition",
        "-i": "220",
        "-t": "9001",
        "-s": "openvox.home.arpa",
        "-m": "16384",
        "-d": "1000G",
        "-a": "bc:24:11:4e:c0:22",
        "-v": "virtio",
    }


@pytest.mark.parametrize("vga", ["virtio", "serial0", "invalid"])
def test_virtual_graphics_before_start(tmp_path: Path, vga: str) -> None:
    """Validate graphics before mutation and configure it before guest start."""
    result, calls = run_atlas(tmp_path, arguments=["-v", vga])
    assert (result.returncode == 0) == (vga != "invalid"), result.stderr
    if vga == "invalid":
        assert calls == []
    else:
        assert calls.index(f"qm set 210 --vga {vga}") < calls.index("qm start 210")


@pytest.mark.parametrize("root_count", [0, 1, 2])
def test_docker_infrastructure_trust(tmp_path: Path, root_count: int) -> None:
    """Reject absent or ambiguous trust before enrolling the FreeIPA client."""
    config = parse_cloud_template(render_cloud_template(tmp_path, "docker"))
    command = next(
        step
        for step in config["runcmd"]
        if isinstance(step, str) and "roots.one?" in step
    )
    args = shlex.split(command)
    root = (ROOT / "docker/wolf/proxmox-cortex-pve-root-ca.pem").read_text()
    bundle = tmp_path / "bundle.pem"
    bundle.write_text(root * root_count)
    installed = tmp_path / "infrastructure.crt"
    result = subprocess.run(
        [*args[:-2], str(bundle), str(installed)], capture_output=True, text=True
    )
    assert (result.returncode == 0) == (root_count == 1), result.stderr
    assert installed.read_text() == root if root_count == 1 else not installed.exists()
    install = config["runcmd"].index(command)
    update = config["runcmd"].index("update-ca-certificates")
    enroll = next(
        i
        for i, step in enumerate(config["runcmd"])
        if isinstance(step, list) and step[0] == "/usr/local/bin/configure-puppet.sh"
    )
    assert install < update < enroll
