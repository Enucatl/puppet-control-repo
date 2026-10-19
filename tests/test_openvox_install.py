"""Check native package selection and authenticated archive installation safely."""

import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
FINGERPRINT = "775661C709ACE6F7C1CF0FF6C4B7528F03E0BB94"


@pytest.mark.parametrize(
    "system,target,fingerprint,installed,accepted",
    [
        ("ubuntu:24.04:amd64", "ubuntu24.04", FINGERPRINT, True, True),
        ("ubuntu:26.04:amd64", "ubuntu26.04", FINGERPRINT, True, True),
        ("debian:13:amd64", "debian13", FINGERPRINT, True, True),
        ("debian:12:amd64", "", FINGERPRINT, True, False),
        ("ubuntu:26.04:arm64", "", FINGERPRINT, True, False),
        ("ubuntu:24.04:amd64", "ubuntu24.04", "UNTRUSTED", True, False),
        ("ubuntu:24.04:amd64", "ubuntu24.04", FINGERPRINT, False, False),
    ],
)
def test_native_install_pins_supported_signed_package(
    tmp_path: Path,
    system: str,
    target: str,
    fingerprint: str,
    installed: bool,
    accepted: bool,
) -> None:
    """Reject unsupported machines, wrong signing keys and mismatched packages."""
    distribution, version, architecture = system.split(":")
    directory = tmp_path / "etc"
    directory.mkdir()
    (directory / "os-release").write_text(
        f'ID={distribution}\nVERSION_ID="{version}"\n'
    )
    (directory / "apt/sources.list.d").mkdir(parents=True)
    commands = tmp_path / "commands"
    binaries = tmp_path / "bin"
    binaries.mkdir()
    replacements = {
        "id": "echo 0",
        "dpkg": f"echo {architecture}",
        "dpkg-query": f"echo {'9.1.0+homelab.24001-1+' + target if installed else 'wrong-version'}",
        "gpg": f"printf 'pub:::::::::\\nfpr:::::::::{fingerprint}:\\n'",
        "curl": 'while [ "$#" -gt 0 ]; do if [ "$1" = --output ]; then printf "fixture\\n" > "$2"; exit; fi; shift; done; exit 1',
        "apt-get": ":",
        "systemctl": ":",
        "puppet": 'if [ "$1 $2 $3" = "config print key_type" ]; then echo mldsa44; fi',
        "ruby": ":",
    }
    for name, body in replacements.items():
        path = binaries / name
        path.write_text(
            '#!/bin/bash\nprintf "%s %s\\n" "${0##*/}" "$*" >> "$COMMAND_LOG"\n'
            + body
            + "\n"
        )
        path.chmod(0o755)
    script = (ROOT / "proxmox/install-openvox.sh").read_text()
    script = script.replace("/etc/", str(directory) + "/")
    script = script.replace("/opt/puppetlabs/bin/puppet", str(binaries / "puppet"))
    script = script.replace("/opt/puppetlabs/puppet/bin/ruby", str(binaries / "ruby"))
    result = subprocess.run(
        ["bash", "-c", script],
        env={
            **os.environ,
            "PATH": f"{binaries}:{os.environ['PATH']}",
            "COMMAND_LOG": str(commands),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert (result.returncode == 0) == accepted, result.stderr
    calls = commands.read_text()
    if not target:
        assert "apt-get" not in calls
        assert "systemctl" not in calls
    elif fingerprint != FINGERPRINT:
        assert "apt-get install -y openvox-agent" not in calls
        assert not (directory / "apt/sources.list.d/openvox-homelab.list").exists()
    else:
        assert (
            f"apt-get install -y openvox-agent=9.1.0+homelab.24001-1+{target}" in calls
        )
        assert f"resolute-24001/{target}.pref" in calls
        source = (directory / "apt/sources.list.d/openvox-homelab.list").read_text()
        assert "signed-by=" in source
        if accepted:
            assert "key_type mldsa44 --section agent" in calls
            assert "systemctl unmask" not in calls
            assert (
                "Groups = X25519MLKEM768"
                in (directory / "puppetlabs/puppet/openssl.cnf").read_text()
            )
