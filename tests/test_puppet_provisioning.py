"""Check enrollment issuance and rendered guest scripts without real machines."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
PROXMOX = ROOT / "proxmox"
CERTNAME = "test-vm.home.arpa"
TOKEN = "hvs.dummy-enrollment-token"
AUTH = {
    "client_token": TOKEN,
    "policies": ["puppet-enrollment"],
    "metadata": {"certname": CERTNAME},
    "token_type": "service",
    "orphan": True,
    "renewable": False,
    "num_uses": 1,
    "lease_duration": 7200,
}


def issue_token(
    tmp_path: Path,
    auth: dict[str, object],
    certname: str | None = CERTNAME,
    curl_status: int = 0,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run the real helper with a recording curl replacement."""
    call_file = tmp_path / "curl-args"
    script = r"""
set -euo pipefail
source "$1"
shift
curl() {
    printf '%s\0' "$@" >> "$CALL_FILE"
    printf '%s' "$RESPONSE"
    return "$CURL_STATUS"
}
create_vault_token "$@"
test "$VM_TOKEN" = "$EXPECTED_TOKEN"
"""
    result = subprocess.run(
        ["bash", "-c", script, "test", str(PROXMOX / "lib.sh")]
        + ([] if certname is None else [certname]),
        env={
            **os.environ,
            "VAULT_TOKEN": "dummy-administrator-token",
            "VAULT_ADDR": "https://vault.example:8200",
            "VAULT_CACERT": "/tmp/test ca.pem",
            "CALL_FILE": str(call_file),
            "RESPONSE": json.dumps({"auth": auth}),
            "CURL_STATUS": str(curl_status),
            "EXPECTED_TOKEN": TOKEN,
        },
        text=True,
        capture_output=True,
        check=False,
    )
    args = call_file.read_text().rstrip("\0").split("\0") if call_file.exists() else []
    assert TOKEN not in result.stdout + result.stderr
    return result, args


def test_token_creation_uses_one_verified_orphan_request(tmp_path: Path) -> None:
    """Request a restricted service token and inspect it without consuming it."""
    result, args = issue_token(tmp_path, AUTH)
    assert result.returncode == 0, result.stderr
    assert args.count("--request") == 1
    assert args[-1] == "https://vault.example:8200/v1/auth/token/create-orphan"
    assert "--insecure" not in args and "-k" not in args
    assert "--fail" in args
    assert args[args.index("--cacert") + 1] == "/tmp/test ca.pem"
    assert json.loads(args[args.index("--data") + 1]) == {
        "policies": ["puppet-enrollment"],
        "type": "service",
        "ttl": "2h",
        "num_uses": 1,
        "renewable": False,
        "no_default_policy": True,
        "meta": {"certname": CERTNAME},
    }


def test_token_metadata_is_json_encoded(tmp_path: Path) -> None:
    """Keep JSON metacharacters in the value rather than the request shape."""
    certname = 'test"\\name.home.arpa'
    result, args = issue_token(
        tmp_path, {**AUTH, "metadata": {"certname": certname}}, certname
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(args[args.index("--data") + 1])["meta"] == {"certname": certname}


@pytest.mark.parametrize(
    "changed",
    [
        {"client_token": None},
        {"client_token": ""},
        {"policies": ["puppet"]},
        {"policies": ["default", "puppet-enrollment"]},
        {"metadata": {}},
        {"metadata": {"certname": "other.home.arpa"}},
        {"token_type": "batch"},
        {"orphan": False},
        {"renewable": True},
        {"num_uses": 0},
        {"lease_duration": 0},
        {"lease_duration": 7201},
        {"lease_duration": "7200"},
    ],
)
def test_invalid_creation_response_fails(
    tmp_path: Path, changed: dict[str, object]
) -> None:
    """Fail closed when Vault does not return the requested constraints."""
    result, _ = issue_token(tmp_path, {**AUTH, **changed})
    assert result.returncode != 0


def test_failed_request_and_missing_certname(tmp_path: Path) -> None:
    """Do not accept a failed request or issue a token without its identity."""
    result, args = issue_token(tmp_path, AUTH, certname=None)
    assert result.returncode != 0
    assert args == []
    result, _ = issue_token(tmp_path, AUTH, curl_status=22)
    assert result.returncode != 0


@pytest.mark.parametrize(
    "kind", ["docker", "desktop", "freeipa-client-acceptance", "host"]
)
def test_rendered_enrollment_configures_exact_identity(
    tmp_path: Path, kind: str
) -> None:
    """Execute embedded scripts with dummy commands and a private filesystem."""
    node_type = {"docker": "docker", "desktop": "desktop", "host": "proxmox"}.get(
        kind, "freeipa_acceptance"
    )
    if kind == "host":
        script = (PROXMOX / "configure-puppet.sh").read_text()
        args = [node_type, TOKEN, CERTNAME, "puppet.example"]
        caller = (PROXMOX / "enroll-proxmox.sh").read_text()
        assert caller.index(
            "systemctl mask --runtime --now puppet.service"
        ) < caller.index("apt-get install -y puppet-agent")
    else:
        caller = {
            "docker": "docker-server.sh",
            "desktop": "desktop.sh",
            "freeipa-client-acceptance": "freeipa-client-acceptance.sh",
        }[kind]
        substitutions = re.search(r"envsubst '([^']+)'", (PROXMOX / caller).read_text())
        assert substitutions is not None
        rendered = subprocess.run(
            ["envsubst", substitutions[1]],
            input=(PROXMOX / f"{kind}-cloud-init.yml.tmpl").read_text(),
            env={
                **os.environ,
                "VM_TOKEN": TOKEN,
                "VM_FQDN": CERTNAME,
                "NODE_TYPE": node_type,
                "PUPPET_SERVER": "puppet.example",
                "DOMAIN_SUFFIX": "home.arpa",
                "IPV6_POOL": "fd00:1234:5678:a000::/56",
                "IPV6_FIXED": "fd00:1234:5678:a100::/64",
            },
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        assert (
            "bootcmd:\n  - cloud-init-per instance puppet-enrollment-mask "
            "systemctl mask --runtime --now puppet.service\n" in rendered
        )
        embedded = re.search(r"    content: \|\n((?:      .*\n|\n)+)", rendered)
        assert embedded is not None
        script = "\n".join(line[6:] for line in embedded[1].splitlines())
        invocation = re.search(
            r"^  - /usr/local/(?:bin|sbin)/configure-puppet[^\n]*", rendered, re.M
        )
        assert invocation is not None
        args = shlex.split(invocation[0])[2:]

    # Absolute production paths are redirected before any script is executed.
    script = script.replace("/etc/puppetlabs/puppet", str(tmp_path / "puppet"))
    script = script.replace("/etc/facter/facts.d", str(tmp_path / "facts"))
    script = script.replace("/opt/puppetlabs/bin/puppet", "puppet")
    for command in ["puppet", "systemctl"]:
        executable = (
            tmp_path / command if command == "systemctl" else tmp_path / "bin" / command
        )
        executable.parent.mkdir(exist_ok=True)
        executable.write_text(
            '#!/bin/bash\nprintf "%s %s\\n" "${0##*/}" "$*" >> "$COMMAND_LOG"\n'
        )
        executable.chmod(0o755)
    subprocess.run(["bash", "-n"], input=script, text=True, check=True)
    subprocess.run(
        ["bash", "-c", script, "test", *args],
        env={
            **os.environ,
            "PATH": f"{tmp_path / 'bin'}:{tmp_path}:{os.environ['PATH']}",
            "COMMAND_LOG": str(tmp_path / "commands"),
        },
        text=True,
        capture_output=True,
        check=True,
    )
    assert (tmp_path / "puppet" / "csr_attributes.yaml").read_text() == (
        f'custom_attributes:\n  1.2.840.113549.1.9.7: "{TOKEN}"\n'
    )
    assert (
        tmp_path / "facts" / "node_type.txt"
    ).read_text() == f"node_type={node_type}\n"
    commands = (tmp_path / "commands").read_text().splitlines()
    assert commands[0] == "systemctl stop puppet"
    assert "puppet config set server puppet.example --section main" in commands
    assert (
        commands.index(f"puppet config set certname {CERTNAME} --section main")
        < commands.index("systemctl unmask --runtime puppet.service")
        < commands.index("systemctl enable puppet")
        < commands.index("puppet agent --test --waitforlock 300")
    )


def test_base_template_leaves_puppet_disabled() -> None:
    """Prevent a cloned guest from running Puppet before its first cloud-init."""
    template = (PROXMOX / "ubuntu-cloud-init.yml.tmpl").read_text()
    assert (
        "bootcmd:\n  - cloud-init-per instance puppet-enrollment-mask "
        "systemctl mask --runtime --now puppet.service\n" in template
    )
    assert template.index("systemctl unmask --runtime puppet.service") < template.index(
        "systemctl disable --now puppet.service"
    )
    assert "systemctl enable puppet" not in template
