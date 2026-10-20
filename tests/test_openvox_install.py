"""Check native package selection and authenticated archive installation safely."""

import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
FINGERPRINT = "775661C709ACE6F7C1CF0FF6C4B7528F03E0BB94"


@pytest.mark.parametrize(
    "system,target,fingerprint,wrong_package,accepted",
    [
        ("ubuntu:24.04:amd64", "ubuntu24.04", FINGERPRINT, "", True),
        ("ubuntu:26.04:amd64", "ubuntu26.04", FINGERPRINT, "", True),
        ("debian:13:amd64", "debian13", FINGERPRINT, "", True),
        ("ubuntu:26.04:amd64", "ubuntu26.04", FINGERPRINT, "curl", False),
        ("ubuntu:26.04:amd64", "ubuntu26.04", FINGERPRINT, "libcurl4t64", False),
        ("debian:13:amd64", "debian13", FINGERPRINT, "python3-ipalib", False),
        ("debian:12:amd64", "", FINGERPRINT, "", False),
        ("ubuntu:26.04:arm64", "", FINGERPRINT, "", False),
        ("ubuntu:24.04:amd64", "ubuntu24.04", "UNTRUSTED", "", False),
        ("ubuntu:24.04:amd64", "ubuntu24.04", FINGERPRINT, "openvox-agent", False),
    ],
)
def test_native_install_pins_supported_signed_package(
    tmp_path: Path,
    system: str,
    target: str,
    fingerprint: str,
    wrong_package: str,
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
    versions = {"openvox-agent": f"9.1.0+homelab.24001-1+{target}"}
    if target in {"ubuntu26.04", "debian13"}:
        curl_version, ipa_version = (
            ("8.18.0-1ubuntu2.8", "4.13.1-1")
            if target == "ubuntu26.04"
            else ("8.14.1-2+deb13u7", "4.12.4-1")
        )
        versions.update(
            curl=f"{curl_version}+homelab.1+{target}",
            libcurl4t64=f"{curl_version}+homelab.1+{target}",
            **{"python3-ipalib": f"{ipa_version}+homelab.1+{target}"},
        )
    queries = (
        'case "${@: -1}" in '
        + " ".join(
            f"{name}) echo {'wrong-version' if name == wrong_package else version} ;;"
            for name, version in versions.items()
        )
        + " *) exit 1 ;; esac"
    )
    replacements = {
        "id": "echo 0",
        "dpkg": f"echo {architecture}",
        "dpkg-query": queries,
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
    script = script.replace("/usr/local/libexec", str(tmp_path / "libexec"))
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
        if target in {"ubuntu26.04", "debian13"}:
            assert f"native-client-2-1/native-client-{target}.pref" in calls
            native_source = (
                directory / "apt/sources.list.d/openvox-native-client.list"
            ).read_text()
            assert "signed-by=" in native_source
            for name, version in versions.items():
                assert f"{name}={version}" in calls
        else:
            assert not (
                directory / "apt/sources.list.d/openvox-native-client.list"
            ).exists()
        assert "--allow-downgrades" not in calls
        assert f"resolute-24001/{target}.pref" in calls
        source = (directory / "apt/sources.list.d/openvox-homelab.list").read_text()
        assert "signed-by=" in source
        if accepted:
            shim = (tmp_path / "libexec/openvox-child-policy.rb").read_text()
            assert shim == (
                ROOT / "modules/profile/files/openvox-child-policy.rb"
            ).read_text().replace("/etc/", str(directory) + "/")
            dropin = (
                directory / "systemd/system/puppet.service.d/puppet-crypto.conf"
            ).read_text()
            assert f"-r {tmp_path / 'libexec/openvox-child-policy.rb'}" in dropin
            assert "key_type mldsa44 --section agent" in calls
            assert "systemctl unmask" not in calls
            assert (
                "Groups = X25519MLKEM768"
                in (directory / "puppetlabs/puppet/openssl.cnf").read_text()
            )
