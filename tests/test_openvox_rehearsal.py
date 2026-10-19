"""Check rehearsal isolation and identity preservation without starting containers."""

import importlib.util
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import shutil
import sys

import pytest


REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "lifetime,accepted", [(90000, True), (3600, False), (-3600, False)]
)
def test_import_requires_issuer_outliving_server(lifetime: int, accepted: bool) -> None:
    """Refuse an issuer that cannot cover the CA CLI's configured leaf lifetime."""
    ruby = shutil.which("ruby")
    if ruby is None:
        pytest.skip("Ruby is required to check the CA import guard")
    result = subprocess.run(
        [
            ruby,
            "-e",
            r"""
require 'openssl'
require 'fileutils'
key = OpenSSL::PKey::RSA.new(2048)
cert = OpenSSL::X509::Certificate.new
cert.serial = 1
cert.version = 2
cert.subject = cert.issuer = OpenSSL::X509::Name.parse('/CN=Test issuer')
cert.public_key = key
cert.not_before = Time.now - 7200
cert.not_after = Time.now + Integer(ARGV.shift)
cert.sign(key, OpenSSL::Digest::SHA256.new)
script = ARGV.shift
original_read = File.method(:read)
File.define_singleton_method(:read) { |path| path.end_with?('/import.pem') ? cert.to_pem : original_read.call(path) }
FileUtils.define_singleton_method(:mkdir_p) { |*_paths| nil }
ARGV.replace(['check-issuer'])
load script
""",
            "--",
            str(lifetime),
            str(REPO / "docker/openvox/pki.rb"),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert (result.returncode == 0) == accepted, result.stderr
    if not accepted:
        assert "issuer must outlive" in result.stderr


@pytest.fixture
def rehearsal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Load an isolated controller with disposable local work paths."""
    monkeypatch.syspath_prepend(str(REPO / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "openvox_rehearsal", REPO / "scripts/openvox-rehearsal.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "WORK", tmp_path / "work")
    module.WORK.mkdir()
    monkeypatch.setattr(module, "STAGE", tmp_path / "work/repo")
    monkeypatch.setattr(module, "REPORT", {"checks": [], "blockers": []})
    return module


def test_compose_pins_daemon_project_and_environment(
    rehearsal, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ignore ambient Compose configuration and explicitly select local Docker."""
    monkeypatch.setenv("COMPOSE_ENV_FILES", "/production/credentials.env")
    monkeypatch.setenv("COMPOSE_FILE", "/production/compose.yaml")
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "production")
    monkeypatch.setenv("DOCKER_HOST", "ssh://docker.home.arpa")
    calls = []

    def run(arguments, **kwargs):
        """Capture subprocess inputs without invoking Docker."""
        calls.append((arguments, kwargs))
        return subprocess.CompletedProcess(arguments, 0, b"ok", b"")

    monkeypatch.setattr(rehearsal.subprocess, "run", run)
    assert rehearsal.compose("config", data="private stdin") == "ok"
    arguments, options = calls[0]
    assert arguments[:3] == ["docker", "--host", "unix:///var/run/docker.sock"]
    assert arguments[3:6] == ["compose", "--env-file", "/dev/null"]
    assert arguments[arguments.index("--project-name") + 1] == "openvox-rehearsal"
    assert arguments[arguments.index("-f") + 1] == str(
        rehearsal.STAGE / "docker/openvox/compose.yaml"
    )
    assert not any(key.startswith("COMPOSE_") for key in options["env"])
    assert options["input"] == b"private stdin"
    assert "private stdin" not in " ".join(arguments)


def test_failure_diagnostics_remain_private(
    rehearsal, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Keep sensitive stdout and stderr out of public exceptions and output."""
    monkeypatch.setattr(
        rehearsal.subprocess,
        "run",
        lambda arguments, **kwargs: subprocess.CompletedProcess(
            arguments, 9, b"secret-token", b"private-key"
        ),
    )
    with pytest.raises(RuntimeError) as failure:
        rehearsal.execute(["failure"])
    message = str(failure.value)
    assert "secret-token" not in message
    assert "private-key" not in message
    log = next((rehearsal.WORK / "private").glob("failure-*.log"))
    assert log.read_bytes() == b"secret-tokenprivate-key"
    assert stat.S_IMODE(log.stat().st_mode) == 0o600
    assert stat.S_IMODE(log.parent.stat().st_mode) == 0o700
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("revoked_denied", (False, True))
def test_outbound_checks_preserve_evidence_and_fail_on_revoked_peer(
    rehearsal, monkeypatch: pytest.MonkeyPatch, revoked_denied: bool
) -> None:
    """A working client cannot mask a missing revocation check or erase evidence."""
    monkeypatch.setattr(rehearsal.shutil, "copyfile", lambda *_args: None)
    scenarios = (
        "valid",
        "classical_only",
        "tls12_only",
        "wrong_hostname",
        "untrusted",
        "expired",
        "revoked",
    )
    results = {
        name: {
            "python_autosigner_policy_passed": name != "revoked" or revoked_denied,
            "jruby_hiera_policy_passed": True,
        }
        for name in scenarios
    }
    results["positive_handshakes"] = {
        "python_token_only": True,
        "jruby_mtls": True,
        "tls13_hybrid_mldsa": True,
    }
    evidence = {"results": results, "handshakes": []}
    monkeypatch.setattr(rehearsal, "inside", lambda *_args: json.dumps(evidence))
    if revoked_denied:
        rehearsal.outbound_acceptance()
    else:
        with pytest.raises(RuntimeError, match="unresolved checks"):
            rehearsal.outbound_acceptance()
    assert (
        json.loads((rehearsal.WORK / "outbound-transports.json").read_text())
        == evidence
    )
    assert rehearsal.REPORT == {"checks": [], "blockers": []}


@pytest.mark.parametrize("status", (200, 204, 501, 503))
def test_api_accepts_explicit_vault_health_statuses(
    rehearsal, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    """Treat uninitialized and sealed health responses as inspectable state."""
    monkeypatch.setattr(
        rehearsal,
        "inside",
        lambda *args, **kwargs: json.dumps(
            {"status": status, "body": {"initialized": False}}
        ),
    )
    assert rehearsal.api(
        "root", "GET", "sys/health", accepted=(200, 204, 501, 503)
    ) == {"initialized": False}


def test_api_rejects_unexpected_status_without_exposing_body(
    rehearsal, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Report a failed endpoint without publishing its potentially secret body."""
    monkeypatch.setattr(
        rehearsal,
        "inside",
        lambda *args, **kwargs: '{"status":403,"body":{"token":"private"}}',
    )
    with pytest.raises(RuntimeError, match="returned HTTP 403") as failure:
        rehearsal.api("vault", "GET", "sys/health")
    assert "private" not in str(failure.value)


@pytest.mark.parametrize("contents", (b"ordinary text\n", "Zürich 🐈\n".encode()))
def test_stage_preserves_archive_and_excludes_local_environment(
    rehearsal, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, contents: bytes
) -> None:
    """Stage real Git inputs intact while excluding untracked environment files."""
    source = tmp_path / "source"
    source.mkdir()
    for relative, value in {
        "Puppetfile": b"forge 'https://forgeapi.puppet.com'\n",
        "scripts/autosign.py": b"# autosign\n",
        "scripts/external_node_classifier.py": b"# classifier\n",
        "scripts/openvox-rehearsal.py": b"# controller\n",
        "scripts/openvox_rehearsal_recovery.py": b"# recovery\n",
        "scripts/openvox_offline_root.py": b"# offline root ceremony\n",
        "docker/openvox/compose.yaml": b"services: {}\n",
        "README": contents,
    }.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    for arguments in (
        ["init", "--initial-branch=production"],
        ["add", "."],
        [
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "Inputs",
        ],
    ):
        subprocess.run(
            ["git", "-C", str(source), *arguments], check=True, capture_output=True
        )
    for relative in (".env", "docker/openvox/.env", "docker/openvox/.env.production"):
        (source / relative).write_text("SECRET=production-token\n")
    monkeypatch.setattr(rehearsal, "SOURCE", source)
    original_execute = rehearsal.execute

    def execute(arguments, *args, **kwargs):
        """Use real Git commands and a read-only fake Docker model."""
        if arguments[0] == "docker":
            if "info" in arguments:
                return socket.gethostname()
            assert "config" in arguments
            return '{"services":{},"networks":{}}'
        return original_execute(arguments, *args, **kwargs)

    monkeypatch.setattr(rehearsal, "execute", execute)
    rehearsal.STAGE.mkdir()
    (rehearsal.STAGE / "docker/openvox").mkdir(parents=True)
    prior_config = rehearsal.STAGE / "docker/openvox/compose.yaml"
    prior_config.write_text("old configuration")
    config_inode = prior_config.stat().st_ino
    directory_inode = prior_config.parent.stat().st_ino
    (rehearsal.STAGE / "retired.pp").write_text("old tracked manifest")
    (rehearsal.STAGE / ".env").write_text("OLD_SECRET=private")
    rehearsal.stage()
    assert (rehearsal.STAGE / "README").read_bytes() == contents
    assert not list(rehearsal.STAGE.rglob(".env*"))
    assert not (rehearsal.STAGE / "retired.pp").exists()
    assert prior_config.stat().st_ino == config_inode
    assert prior_config.parent.stat().st_ino == directory_inode


def test_existing_vault_unseals_without_creating_new_identity(
    rehearsal, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restore existing recovery data without regenerating keys or initializing."""
    commands, requests = [], []

    def inside(service, arguments, *args, **kwargs):
        """Return only preexisting transport and recovery state."""
        commands.append(arguments)
        if arguments[0] == "sh":
            return "existing"
        assert arguments == ["cat", "/state/recovery.json"]
        return '{"keys_base64":["private-unseal-key"]}'

    def api(service, method, path, body=None, **kwargs):
        """Capture initialization requests without contacting Vault."""
        requests.append((path, body))
        if path == "sys/health":
            return {"initialized": True, "sealed": True}
        assert path == "sys/unseal"
        return {}

    monkeypatch.setattr(rehearsal, "inside", inside)
    monkeypatch.setattr(rehearsal, "api", api)
    monkeypatch.setattr(rehearsal, "compose", lambda *args, **kwargs: "")
    rehearsal.start_vault("vault")
    assert [path for path, _ in requests] == ["sys/health", "sys/unseal"]
    assert requests[-1][1] == {"key": "private-unseal-key"}
    assert not any("transport.rb" in " ".join(command) for command in commands)


def test_existing_mount_is_not_replaced(
    rehearsal, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preserve restored PKI mounts instead of replacing their authorities."""
    calls = []

    def api(service, method, path, *args, **kwargs):
        """Reject any mutation of an existing authority."""
        calls.append((service, method, path))
        assert method == "GET"
        return {"data": {"pki/": {"type": "pki"}}}

    monkeypatch.setattr(rehearsal, "api", api)
    assert rehearsal.mount("root", "pki", "pki") is False
    assert calls == [("root", "GET", "sys/mounts")]


@pytest.mark.parametrize("transport_exists", (False, True))
def test_normal_start_refuses_to_replace_missing_vault_state(
    rehearsal, monkeypatch: pytest.MonkeyPatch, transport_exists: bool
) -> None:
    """Require explicit initialization if either transport or authority is absent."""
    monkeypatch.setattr(
        rehearsal,
        "inside",
        lambda *args, **kwargs: "existing" if transport_exists else "",
    )
    monkeypatch.setattr(rehearsal, "compose", lambda *args, **kwargs: "")
    requests = []

    def api(service, method, path, *args, **kwargs):
        """Expose uninitialized storage and reject attempts to initialize it."""
        requests.append(path)
        assert method == "GET" and path == "sys/health"
        return {"initialized": False, "sealed": True}

    monkeypatch.setattr(rehearsal, "api", api)
    with pytest.raises(RuntimeError, match="Missing established Vault"):
        rehearsal.start_vault("vault")
    assert requests == (["sys/health"] if transport_exists else [])


def test_initialize_does_not_resign_restored_authorities(
    rehearsal, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reuse restored root, OpenVox CA and FreeIPA intermediate identities."""
    commands, requests = [], []

    def api(service, method, path, *args, **kwargs):
        """Allow only reads of the restored root's public certificate and CRL."""
        requests.append((service, method, path))
        assert (service, method, path) in (
            ("root", "GET", "pki/cert/ca"),
            ("root", "GET", "pki/cert/crl"),
            ("vault", "GET", "pki_int/cert/crl"),
        )
        return {
            "data": {
                "certificate": "-----BEGIN CERTIFICATE-----\nAQ==\n-----END CERTIFICATE-----"
            }
        }

    def inside(service, arguments, *args, **kwargs):
        """Return the already imported CA and prepared intermediate request."""
        commands.append(arguments)
        if arguments[0] == "sh":
            return "existing"
        if arguments == ["cat", "/trust/root.crl"]:
            return "restored root CRL"
        assert arguments == [rehearsal.RUBY, "/rehearsal/pki.rb", "prepare"]
        return "existing CSR"

    monkeypatch.setattr(rehearsal, "api", api)
    monkeypatch.setattr(rehearsal, "inside", inside)
    monkeypatch.setattr(rehearsal, "start_vault", lambda service, **kwargs: None)
    monkeypatch.setattr(rehearsal, "mount", lambda *args, **kwargs: False)
    monkeypatch.setattr(rehearsal, "write", lambda *args, **kwargs: None)
    monkeypatch.setattr(rehearsal, "compose", lambda *args, **kwargs: "")
    monkeypatch.setattr(rehearsal, "configure_access", lambda: None)
    rehearsal.initialize()
    assert len(requests) == 3
    assert not any("import" in command for command in commands)


def test_compose_model_keeps_runtime_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Validate the actual Compose model without Docker daemon mutations."""
    monkeypatch.setenv("COMPOSE_ENV_FILES", "/nonexistent/production.env")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("COMPOSE_")
    }
    result = subprocess.run(
        [
            "docker",
            "--host",
            "unix:///var/run/docker.sock",
            "compose",
            "--env-file",
            "/dev/null",
            "-f",
            str(REPO / "docker/openvox/compose.yaml"),
            "--profile",
            "ceremony",
            "--profile",
            "tools",
            "config",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    model = json.loads(result.stdout)
    assert model["services"]["root"]["network_mode"] == "none"
    assert model["networks"]["default"]["internal"] is True
    assert not any(service.get("ports") for service in model["services"].values())
    assert not any(network.get("external") for network in model["networks"].values())
    for name, service in model["services"].items():
        assert service["user"] == "999:999"
        assert service["image"] == "openvox-rehearsal-tools:18001"
        assert not service.get("env_file")
        if name not in ("root", "deploy"):
            assert set(service["networks"]) == {"default"}


@pytest.mark.parametrize("command", ("up", "stop", "status", "destroy"))
def test_management_can_select_only_the_fixed_restore_project(
    rehearsal, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    """Route documented restored lifecycle commands to the separate local project."""
    monkeypatch.setattr(sys, "argv", ["rehearsal", command, "--restored"])
    operations = []

    def compose(*arguments, **kwargs):
        """Capture the project used for each lifecycle operation."""
        operations.append((rehearsal.PROJECT, arguments))
        return ""

    monkeypatch.setattr(rehearsal, "compose", compose)
    monkeypatch.setattr(
        rehearsal,
        "start_vault",
        lambda service: operations.append((rehearsal.PROJECT, ("unseal", service))),
    )
    rehearsal.main()
    assert operations
    assert all(project == "openvox-rehearsal-restore" for project, _ in operations)
    assert not any(
        "root" in arguments for _, arguments in operations if command == "up"
    )
    if command == "up":
        assert operations[0][1] == ("unseal", "vault")
        assert "--wait" in operations[1][1] and operations[1][1][-1] == "openvox"
        assert operations[-1][1] == ("up", "-d", "--no-deps", "agent")


def test_explicit_initialization_cannot_target_restored_project(
    rehearsal, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reject restored initialization before running any Docker operation."""
    monkeypatch.setattr(sys, "argv", ["rehearsal", "run", "--restored"])

    def unexpected(*args, **kwargs):
        """Fail if argument validation accidentally reaches runtime mutation."""
        pytest.fail("Invalid initialization reached a runtime operation")

    monkeypatch.setattr(rehearsal, "run", unexpected)
    monkeypatch.setattr(rehearsal, "compose", unexpected)
    with pytest.raises(SystemExit) as failure:
        rehearsal.main()
    assert failure.value.code == 2


def test_management_failure_preserves_completed_rehearsal_evidence(
    rehearsal, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep full run evidence intact when a later management operation fails."""
    results = rehearsal.WORK / "results.json"
    evidence = '{"checks":[{"name":"completed rehearsal","passed":true}]}\n'
    results.write_text(evidence)
    monkeypatch.setattr(sys, "argv", ["rehearsal", "up", "--restored"])

    def fail(service):
        """Simulate an unavailable restored Vault without starting containers."""
        raise RuntimeError("Restored Vault unavailable")

    monkeypatch.setattr(rehearsal, "start_vault", fail)
    with pytest.raises(SystemExit, match="Restored Vault unavailable"):
        rehearsal.main()
    assert results.read_text() == evidence
    error = json.loads((rehearsal.WORK / "management-error.json").read_text())
    assert error["error"] == "Restored Vault unavailable"


@pytest.mark.parametrize("target", ("volume", "container"))
def test_recovery_refuses_existing_target_before_stopping_source(
    rehearsal, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """Never overwrite restored state or disturb the source when a target exists."""
    recovery = importlib.import_module("openvox_rehearsal_recovery")

    def execute(arguments, *args, **kwargs):
        """Expose an existing restore volume or container without using Docker."""
        if arguments[3:5] == ["volume", "ls"]:
            return "openvox-rehearsal-restore_server-ca" if target == "volume" else ""
        assert arguments[3:5] == ["ps", "-a"]
        return "restored-container" if target == "container" else ""

    def unexpected(*args, **kwargs):
        """Reject runtime work after the preexisting target check."""
        pytest.fail("Recovery disturbed source despite preexisting target")

    monkeypatch.setattr(rehearsal, "execute", execute)
    monkeypatch.setattr(rehearsal, "identity", unexpected)
    monkeypatch.setattr(rehearsal, "compose", unexpected)
    with pytest.raises(RuntimeError, match="Restore project already exists"):
        recovery.recover(rehearsal)
    assert rehearsal.PROJECT == "openvox-rehearsal"
