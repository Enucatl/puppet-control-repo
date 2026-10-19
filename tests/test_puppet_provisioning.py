"""Check enrollment issuance and rendered guest scripts without real machines."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import textwrap

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

SPEC = importlib.util.spec_from_file_location(
    "desktop_cloud_init", PROXMOX / "desktop_cloud_init.py"
)
assert SPEC is not None and SPEC.loader is not None
DESKTOP_CLOUD_INIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DESKTOP_CLOUD_INIT)


def render_cloud_template(
    tmp_path: Path, kind: str, token: str = TOKEN, server: str = "puppet.example"
) -> str:
    """Run the callers' real template preparation without any VM commands."""
    caller = {
        "docker": "docker-server.sh",
        "freeipa-client-acceptance": "freeipa-client-acceptance.sh",
        "ubuntu": "ubuntu-server-template.sh",
    }[kind]
    source = (PROXMOX / caller).read_text()
    first = (
        "SSH_PUBLIC_KEY_JSON=" if kind == "ubuntu" else 'create_vault_token "$VM_FQDN"'
    )
    start = source.index(first)
    if kind == "docker":
        start = source.rfind("\n", 0, start) + 1
        end = source.index("\nfi\n\n# 2.", start)
    else:
        end = source.index(
            "\nqm clone" if kind == "freeipa-client-acceptance" else "\n# 2.", start
        )
    preparation = textwrap.dedent(source[start:end])
    output = tmp_path / "rendered.yml"
    ca = tmp_path / "public-ca.pem"
    ca.write_text(
        "-----BEGIN CERTIFICATE-----\npublic $TOKEN ' : fixture\n-----END CERTIFICATE-----\n"
    )
    ssh = tmp_path / ".ssh"
    ssh.mkdir(exist_ok=True)
    (ssh / "id_ed25519.pub").write_text('ssh-ed25519 AAAA comment: "$()" \' user\n')
    subprocess.run(
        ["bash", "-euc", "create_vault_token() { :; }\n" + preparation],
        env={
            **os.environ,
            "HOME": str(tmp_path),
            "SCRIPT_DIR": str(PROXMOX),
            "TEMPLATE_SRC": str(PROXMOX / f"{kind}-cloud-init.yml.tmpl"),
            "GENERATED_YAML": str(output),
            "SNIPPET": str(output),
            "SNIPPET_PATH": str(output),
            "OPENVOX_CA_CERT": str(ca),
            "VM_TOKEN": token,
            "VM_FQDN": CERTNAME,
            "NEW_VM_NAME": "test-vm",
            "NODE_TYPE": "docker" if kind == "docker" else "freeipa_acceptance",
            "PUPPET_SERVER": server,
            "DOMAIN_SUFFIX": "home.arpa",
            "IPV6_POOL": "fd00:1234:5678:a000::/56",
            "IPV6_FIXED": "fd00:1234:5678:a100::/64",
        },
        capture_output=True,
        text=True,
        check=True,
    )
    return output.read_text()


def parse_cloud_template(rendered: str) -> dict:
    """Parse full YAML using the existing Ruby runtime without new dependencies."""
    ruby = shutil.which("ruby")
    if ruby is None:
        pytest.skip("Ruby is required to parse cloud-init YAML")
    return json.loads(
        subprocess.run(
            [
                ruby,
                "-ryaml",
                "-rjson",
                "-e",
                "puts JSON.generate(YAML.safe_load(STDIN.read))",
            ],
            input=rendered,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )


@pytest.mark.parametrize("server", [None, "atlas.home.arpa"])
def test_freeipa_caller_loads_endpoint_and_propagates_shared_wait_failure(
    tmp_path: Path, server: str | None
) -> None:
    """Exercise real caller initialization and retain diagnostics on wait failure."""
    scripts = tmp_path / "scripts"
    shutil.copytree(PROXMOX, scripts)
    snippet_dir = tmp_path / "snippets"
    snippet_dir.mkdir()
    caller = scripts / "freeipa-client-acceptance.sh"
    caller.write_text(
        caller.read_text().replace("/var/lib/vz/snippets", str(snippet_dir))
    )
    with (scripts / "lib.sh").open("a") as library:
        library.write(
            "\nvalidate_target() { :; }\n"
            "check_provisioning_host() { :; }\n"
            "create_vault_token() { VM_TOKEN=dummy; }\n"
            "qm() { :; }\n"
            'wait_for_cloudinit() { printf "%s\\n" "$1" > "$WAITED"; return 42; }\n'
        )
    if server:
        (tmp_path / ".env").write_text(f"PUPPET_SERVER={server}\n")
    ca = tmp_path / "ca.pem"
    ca.write_text("public CA fixture\n")
    waited = tmp_path / "waited"
    result = subprocess.run(
        ["bash", str(caller), "211"],
        cwd=tmp_path,
        env={
            **os.environ,
            "VAULT_TOKEN": "dummy-admin",
            "OPENVOX_CA_CERT": str(ca),
            "WAITED": str(waited),
        },
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 42, result.stderr
    assert waited.read_text() == "211\n"
    assert "retaining freeipa-acceptance-211" in result.stderr
    config = parse_cloud_template(
        (snippet_dir / "freeipa-client-acceptance-211.yml").read_text()
    )
    enrollment = next(
        command
        for command in config["runcmd"]
        if isinstance(command, list)
        and command[0] == "/usr/local/sbin/configure-puppet-acceptance"
    )
    assert enrollment[-1] == (server or "docker.home.arpa")


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
@pytest.mark.parametrize("puppet_status", [0, 2, 1, 4, 6])
def test_rendered_enrollment_configures_exact_identity(
    tmp_path: Path, kind: str, puppet_status: int
) -> None:
    """Check identity and exit handling using isolated rendered enrollment scripts."""
    node_type = {"docker": "docker", "desktop": "desktop", "host": "proxmox"}.get(
        kind, "freeipa_acceptance"
    )
    if kind == "host":
        script = (PROXMOX / "configure-puppet.sh").read_text()
        args = [node_type, TOKEN, CERTNAME, "puppet.example"]
        caller = (PROXMOX / "enroll-proxmox.sh").read_text()
        assert caller.index(
            "systemctl mask --runtime --now puppet.service"
        ) < caller.index('bash "$SCRIPT_DIR/install-openvox.sh"')
    elif kind == "desktop":
        config = DESKTOP_CLOUD_INIT.build_cloud_config(
            TOKEN, CERTNAME, "puppet.example", "home.arpa", "authenticated public CA"
        )
        assert config["bootcmd"] == [
            [
                "cloud-init-per",
                "instance",
                "puppet-enrollment-mask",
                "systemctl",
                "mask",
                "--runtime",
                "--now",
                "puppet.service",
            ]
        ]
        script = next(
            item["content"]
            for item in config["write_files"]
            if item["path"] == "/usr/local/bin/configure-puppet.sh"
        )
        assert script == (PROXMOX / "configure-puppet.sh").read_text()
        invocation = next(
            command
            for command in config["runcmd"]
            if isinstance(command, list)
            and command[0] == "/usr/local/bin/configure-puppet.sh"
        )
        args = invocation[1:]
    else:
        rendered = render_cloud_template(tmp_path, kind)
        assert (
            "bootcmd:\n  - cloud-init-per instance puppet-enrollment-mask "
            "systemctl mask --runtime --now puppet.service\n" in rendered
        )
        cloud_config = parse_cloud_template(rendered)
        script = cloud_config["write_files"][0]["content"]
        if kind == "docker":
            assert script.rstrip("\n") == (
                PROXMOX / "configure-puppet.sh"
            ).read_text().rstrip("\n")
        invocation = next(
            command
            for command in cloud_config["runcmd"]
            if isinstance(command, list) and command[0].startswith("/usr/local/")
        )
        args = invocation[1:]

    # Absolute production paths are redirected before any script is executed.
    script = script.replace("/etc/puppetlabs/puppet", str(tmp_path / "puppet"))
    script = script.replace("/etc/facter/facts.d", str(tmp_path / "facts"))
    script = script.replace("/opt/puppetlabs/bin/puppet", "puppet")
    script = script.replace("/opt/puppetlabs/puppet/bin/ruby", shutil.which("ruby"))
    for command in ["puppet", "systemctl"]:
        executable = (
            tmp_path / command if command == "systemctl" else tmp_path / "bin" / command
        )
        executable.parent.mkdir(exist_ok=True)
        executable.write_text(
            '#!/bin/bash\nprintf "%s %s\\n" "${0##*/}" "$*" >> "$COMMAND_LOG"\n'
            + (
                'if [ "$1 $2 $3" = "config print key_type" ]; then echo mldsa44; fi\n'
                'if [ "$1" = agent ]; then exit "$MOCK_PUPPET_STATUS"; fi\n'
                if command == "puppet"
                else ""
            )
        )
        executable.chmod(0o755)
    (tmp_path / "puppet").mkdir()
    (tmp_path / "puppet" / "openssl.cnf").write_text("# installed policy\n")
    (tmp_path / "puppet" / "ssl/certs").mkdir(parents=True)
    (tmp_path / "puppet" / "ssl/certs/ca.pem").write_text("# authenticated trust\n")
    subprocess.run(["bash", "-n"], input=script, text=True, check=True)
    result = subprocess.run(
        ["bash", "-c", script, "test", *args],
        env={
            **os.environ,
            "PATH": f"{tmp_path / 'bin'}:{tmp_path}:{os.environ['PATH']}",
            "COMMAND_LOG": str(tmp_path / "commands"),
            "MOCK_PUPPET_STATUS": str(puppet_status),
        },
        text=True,
        capture_output=True,
        check=False,
    )
    # Acceptance deliberately delegates its authoritative run to the runner.
    expected_status = (
        0
        if kind == "freeipa-client-acceptance" or puppet_status in (0, 2)
        else puppet_status
    )
    assert result.returncode == expected_status, result.stderr
    assert json.loads((tmp_path / "puppet" / "csr_attributes.yaml").read_text()) == {
        "custom_attributes": {"1.2.840.113549.1.9.7": TOKEN}
    }
    assert (
        tmp_path / "facts" / "node_type.txt"
    ).read_text() == f"node_type={node_type}\n"
    commands = (tmp_path / "commands").read_text().splitlines()
    assert commands[0] == (
        "systemctl stop puppet"
        if kind == "freeipa-client-acceptance"
        else f"puppet config print hostprivkey --section agent --certname {CERTNAME}"
    )
    assert commands.index(
        "puppet config set key_type mldsa44 --section agent"
    ) < commands.index("puppet agent --test --waitforlock 300")
    assert "puppet config set server puppet.example --section main" in commands
    assert (
        commands.index(f"puppet config set certname {CERTNAME} --section main")
        < commands.index("systemctl unmask --runtime puppet.service")
        < commands.index("systemctl enable puppet")
        < commands.index("puppet agent --test --waitforlock 300")
    )
    if kind != "freeipa-client-acceptance":
        assert ("puppet resource service puppet ensure=running" in commands) == (
            expected_status == 0
        )

    if kind in ("desktop", "docker"):
        # Exercise the guest shell's failure handling with a successful command
        # after enrollment, without running package or service setup on this host.
        prefix = (
            config["runcmd"][0]
            if kind == "desktop"
            else rendered.split("runcmd:\n", 1)[1].splitlines()[0].removeprefix("  - ")
        )
        enrollment = tmp_path / "enroll.sh"
        enrollment.write_text(script)
        marker = tmp_path / "finalized"
        cloudinit = subprocess.run(
            [
                "sh",
                "-c",
                "\n".join(
                    [
                        prefix,
                        shlex.join(["bash", str(enrollment), *args]),
                        shlex.join(["touch", str(marker)]),
                    ]
                ),
            ],
            env={
                **os.environ,
                "PATH": f"{tmp_path / 'bin'}:{tmp_path}:{os.environ['PATH']}",
                "COMMAND_LOG": str(tmp_path / "commands"),
                "MOCK_PUPPET_STATUS": str(puppet_status),
            },
            text=True,
            capture_output=True,
            check=False,
        )
        assert cloudinit.returncode == expected_status, cloudinit.stderr
        assert marker.exists() == (expected_status == 0)


def test_desktop_enrollment_arguments_survive_serialization() -> None:
    """Keep credential quotes and shell metacharacters as literal arguments."""
    token = "dummy'\"$TOKEN; $(id)\\token\nsecond line"
    config = json.loads(
        json.dumps(
            DESKTOP_CLOUD_INIT.build_cloud_config(
                token,
                CERTNAME,
                "puppet.example",
                "home.arpa",
                "authenticated public CA",
            )
        )
    )
    assert [
        "/usr/local/bin/configure-puppet.sh",
        "desktop",
        token,
        CERTNAME,
        "puppet.example",
    ] in config["runcmd"]
    files = {item["path"]: item for item in config["write_files"]}
    assert (
        files["/usr/local/bin/configure-puppet.sh"]["content"]
        == (PROXMOX / "configure-puppet.sh").read_text()
    )
    assert (
        files["/usr/local/bin/install-openvox.sh"]["content"]
        == (PROXMOX / "install-openvox.sh").read_text()
    )
    assert (
        files["/etc/puppetlabs/puppet/ssl/certs/ca.pem"]["content"]
        == "authenticated public CA"
    )


@pytest.mark.parametrize("kind", ["docker", "freeipa-client-acceptance", "ubuntu"])
def test_full_cloud_template_preserves_scripts_trust_and_literal_arguments(
    tmp_path: Path, kind: str
) -> None:
    """Exercise actual caller preparation, including all newly embedded inputs."""
    token = "dummy'\"$TOKEN; $(id)\\token\nsecond line"
    server = "puppet'\"$SERVER; $(id).example"
    config = parse_cloud_template(render_cloud_template(tmp_path, kind, token, server))
    files = {item["path"]: item for item in config["write_files"]}
    assert (
        files["/usr/local/bin/install-openvox.sh"]["content"]
        == (PROXMOX / "install-openvox.sh").read_text()
    )
    assert files["/usr/local/bin/install-openvox.sh"]["permissions"] == "0755"
    for path, item in files.items():
        if path.endswith(".sh") or path.endswith("configure-puppet-acceptance"):
            subprocess.run(["bash", "-n"], input=item["content"], text=True, check=True)
    if kind == "ubuntu":
        assert config["users"][0]["ssh_authorized_keys"] == [
            'ssh-ed25519 AAAA comment: "$()" \' user'
        ]
        assert "/etc/puppetlabs/puppet/ssl/certs/ca.pem" not in files
    else:
        assert (tmp_path / "rendered.yml").stat().st_mode & 0o777 == 0o600
        assert (
            files["/etc/puppetlabs/puppet/ssl/certs/ca.pem"]["content"]
            == (tmp_path / "public-ca.pem").read_text()
        )
        assert token in next(
            command for command in config["runcmd"] if isinstance(command, list)
        )
        assert server in next(
            command for command in config["runcmd"] if isinstance(command, list)
        )
        if kind == "docker":
            assert (
                files["/usr/local/bin/configure-puppet.sh"]["content"]
                == (PROXMOX / "configure-puppet.sh").read_text()
            )


@pytest.mark.parametrize("kind", ["docker", "freeipa-client-acceptance"])
def test_cloud_template_persists_short_hostname_and_fqdn(
    tmp_path: Path, kind: str
) -> None:
    """Keep the same short hostname and resolver identity after later boots."""
    config = parse_cloud_template(render_cloud_template(tmp_path, kind))
    expected = {
        "hostname": "test-vm",
        "fqdn": CERTNAME,
        "prefer_fqdn_over_hostname": False,
        "manage_etc_hosts": True,
    }
    assert {key: config[key] for key in expected} == expected
    persistent = next(
        item["content"]
        for item in config["write_files"]
        if item["path"] == "/etc/cloud/cloud.cfg.d/99-hostname.cfg"
    )
    assert parse_cloud_template(persistent) == expected


@pytest.mark.parametrize("kind", ["host", "freeipa-client-acceptance"])
def test_enrollment_refuses_existing_rsa_before_unmasking(
    tmp_path: Path, kind: str
) -> None:
    """Preserve existing identities instead of relabeling an RSA key as ML-DSA."""
    if kind == "host":
        script = (PROXMOX / "configure-puppet.sh").read_text()
        args = ["proxmox", TOKEN, CERTNAME, "puppet.example"]
    else:
        config = parse_cloud_template(render_cloud_template(tmp_path, kind))
        script = config["write_files"][0]["content"]
        args = next(
            command for command in config["runcmd"] if isinstance(command, list)
        )[1:]
    ruby = shutil.which("ruby")
    if ruby is None:
        pytest.skip("Ruby is required to validate native identities")
    key = tmp_path / "existing-rsa.pem"
    subprocess.run(
        [
            ruby,
            "-ropenssl",
            "-e",
            "File.write(ARGV.fetch(0), OpenSSL::PKey::RSA.new(2048).to_pem)",
            str(key),
        ],
        check=True,
    )
    original = key.read_bytes()
    bins = tmp_path / "bin"
    bins.mkdir()
    log = tmp_path / "commands"
    for name, body in {
        "systemctl": ":",
        "puppet": 'case "$1 $2 $3" in "config print key_type") echo mldsa44 ;; "config print hostprivkey") printf "%s\\n" "$HOST_KEY" ;; esac',
    }.items():
        path = bins / name
        path.write_text(
            '#!/bin/bash\nprintf "%s %s\\n" "${0##*/}" "$*" >> "$COMMAND_LOG"\n'
            + body
            + "\n"
        )
        path.chmod(0o755)
    puppet = tmp_path / "puppet"
    (puppet / "ssl/certs").mkdir(parents=True)
    (puppet / "ssl/certs/ca.pem").write_text("authenticated trust")
    (puppet / "openssl.cnf").write_text("# installed policy\n")
    script = script.replace("/etc/puppetlabs/puppet", str(puppet)).replace(
        "/etc/facter/facts.d", str(tmp_path / "facts")
    )
    script = script.replace("/opt/puppetlabs/bin/puppet", "puppet").replace(
        "/opt/puppetlabs/puppet/bin/ruby", ruby
    )
    result = subprocess.run(
        ["bash", "-c", script, "test", *args],
        env={
            **os.environ,
            "PATH": f"{bins}:{os.environ['PATH']}",
            "COMMAND_LOG": str(log),
            "HOST_KEY": str(key),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "Existing identity is not ML-DSA-44" in result.stderr
    assert "systemctl unmask" not in log.read_text()
    assert "puppet agent" not in log.read_text()
    if kind == "host":
        assert "systemctl" not in log.read_text()
        assert "puppet config set" not in log.read_text()
        assert not (puppet / "csr_attributes.yaml").exists()
    assert key.read_bytes() == original


@pytest.mark.parametrize(
    "scenario", ["missing_ca", "different_ca", "rsa_current", "rsa_target", "fresh"]
)
def test_host_preflight_precedes_all_mutations(tmp_path: Path, scenario: str) -> None:
    """Refuse incompatible hosts before token issuance, service or package changes."""
    ruby = shutil.which("ruby")
    if ruby is None:
        pytest.skip("Ruby is required to validate native identities")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    puppet = tmp_path / "puppet"
    (puppet / "ssl/certs").mkdir(parents=True)
    trust = puppet / "ssl/certs/ca.pem"
    trust.write_text("existing authenticated trust")
    ca = tmp_path / "authenticated-ca.pem"
    if scenario != "missing_ca":
        ca.write_text(
            "different trust" if scenario == "different_ca" else trust.read_text()
        )
    key = tmp_path / "existing-rsa.pem"
    subprocess.run(
        [
            ruby,
            "-ropenssl",
            "-e",
            "File.write(ARGV.fetch(0), OpenSSL::PKey::RSA.new(2048).to_pem)",
            str(key),
        ],
        check=True,
    )
    original = key.read_bytes()
    commands = tmp_path / "commands"
    bins = tmp_path / "bin"
    bins.mkdir()
    for name, body in {
        "systemctl": ":",
        "puppet": (
            'if [[ "$*" == *--certname* && "$SCENARIO" == rsa_target || '
            '"$*" != *--certname* && "$SCENARIO" == rsa_current ]]; then '
            'printf "%s\\n" "$HOST_KEY"; fi'
        ),
    }.items():
        path = bins / name
        path.write_text(
            '#!/bin/bash\nprintf "%s %s\\n" "${0##*/}" "$*" >> "$COMMAND_LOG"\n'
            + body
            + "\n"
        )
        path.chmod(0o755)
    (scripts / "config.sh").write_text('PUPPET_SERVER="puppet.example"\n')
    (scripts / "lib.sh").write_text(
        'load_env() { :; }\ncreate_vault_token() { echo token >> "$COMMAND_LOG"; VM_TOKEN=dummy; }\n'
    )
    (scripts / "install-openvox.sh").write_text(
        'echo install >> "$COMMAND_LOG"\nexit 42\n'
    )
    for name in ["enroll-proxmox.sh", "configure-puppet.sh"]:
        script = (PROXMOX / name).read_text()
        script = script.replace("/etc/puppetlabs/puppet", str(puppet))
        script = script.replace("/opt/puppetlabs/bin/puppet", str(bins / "puppet"))
        script = script.replace("/opt/puppetlabs/puppet/bin/ruby", ruby)
        (scripts / name).write_text(script)
        (scripts / name).chmod(0o755)
    result = subprocess.run(
        ["bash", str(scripts / "enroll-proxmox.sh")],
        env={
            **os.environ,
            "PATH": f"{bins}:{os.environ['PATH']}",
            "COMMAND_LOG": str(commands),
            "SCENARIO": scenario,
            "HOST_KEY": str(key),
            "OPENVOX_CA_CERT": str(ca),
            "VAULT_ADDR": "https://unused.example",
            "VAULT_TOKEN": "dummy-admin",
        },
        capture_output=True,
        text=True,
    )
    calls = commands.read_text().splitlines() if commands.exists() else []
    assert result.returncode != 0
    if scenario == "fresh":
        assert result.returncode == 42, result.stderr
        assert calls[-3:] == [
            "token",
            "systemctl mask --runtime --now puppet.service",
            "install",
        ]
    else:
        assert all(call.startswith("puppet config print hostprivkey") for call in calls)
        if scenario.startswith("rsa_"):
            assert "Existing identity is not ML-DSA-44" in result.stderr
    assert key.read_bytes() == original
    assert trust.read_text() == "existing authenticated trust"
    assert not (puppet / "csr_attributes.yaml").exists()


@pytest.mark.parametrize("installer_status", [0, 42])
def test_base_template_propagates_installer_failure(
    tmp_path: Path, installer_status: int
) -> None:
    """Run rendered cloud-init commands without packages or service operations."""
    config = parse_cloud_template(render_cloud_template(tmp_path, "ubuntu"))
    commands = config["runcmd"]
    installer = commands.index("bash /usr/local/bin/install-openvox.sh")
    calls = tmp_path / "commands"
    # Keep shell options and command order, replacing operations with recordings.
    script = "\n".join(
        command
        if command.startswith("set ")
        else f"echo {index} >> {shlex.quote(str(calls))}\n"
        + (f"(exit {installer_status})" if index == installer else ":")
        for index, command in enumerate(commands)
    )
    result = subprocess.run(["sh", "-c", script], capture_output=True, text=True)
    assert result.returncode == installer_status, result.stderr
    last = installer if installer_status else len(commands) - 1
    assert calls.read_text().splitlines()[-1] == str(last)


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
