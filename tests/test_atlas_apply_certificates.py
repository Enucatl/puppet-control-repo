"""Exercise Puppet's service refresh without requesting certificates or credentials."""

from __future__ import annotations

from contextlib import nullcontext
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def controller(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Load service refresh with disposable PEM files and inert Docker boundaries."""
    spec = importlib.util.spec_from_file_location(
        "apply_certificates", ROOT / "proxmox/atlas-apply-certificates.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for attribute, service in (("VAULT_SECRETS", "vault"), ("IPA_SECRETS", "ipa")):
        directory = tmp_path / service
        directory.mkdir()
        (directory / "fullchain.pem").write_text("leaf\nroot\n")
        (directory / "key.pem").write_text("private-key-canary")
        monkeypatch.setattr(module, attribute, directory)
    (module.IPA_SECRETS / "installer.json").write_text(
        json.dumps({"dm_password": "directory-password-canary"})
    )
    monkeypatch.setattr(module, "run", Mock(return_value="PUBLIC KEY"))
    return module


def test_vault_publishes_public_key_before_reload(
    controller: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The unsealer receives the new public key before Vault switches TLS keys."""
    chown = Mock()
    monkeypatch.setattr(controller.os, "chown", chown)
    public = controller.VAULT_SECRETS / "public/vault.pub.pem"

    def run(command: list[str]) -> str:
        """Check publication at the reload boundary."""
        if "kill" in command:
            assert public.read_text() == "PUBLIC KEY"
            assert public.stat().st_mode & 0o777 == 0o644
        return "PUBLIC KEY"

    controller.run.side_effect = run
    controller.apply("vault")
    assert chown.call_args.args[1:] == (100100, 100100)
    assert controller.run.call_args.args[0][-4:] == [
        "kill",
        "--signal",
        "HUP",
        "infra-vault-1",
    ]


@pytest.mark.parametrize("fail", [False, True])
def test_ipa_imports_shared_pair_privately_and_cleans_up(
    controller: ModuleType, fail: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both native stores receive the shared pair without exposing or retaining keys."""
    controller.apply("ipa")
    command, payload = controller.run.call_args_list[0].args
    assert "directory-password-canary" not in str(command)
    assert "private-key-canary" not in str(command)
    options = SimpleNamespace()
    paths = []

    def parse(arguments: list[str]) -> tuple[SimpleNamespace, list[str]]:
        """Inspect secure files at the native install boundary."""
        assert arguments[:3] == ["--http", "--dirsrv", "--pin="]
        paths.extend(Path(path) for path in arguments[3:])
        assert [path.read_text() for path in paths] == [
            "leaf\nroot\n",
            "private-key-canary",
        ]
        assert all(path.stat().st_mode & 0o777 == 0o600 for path in paths)
        assert paths[0].parent.stat().st_mode & 0o777 == 0o700
        return options, arguments[3:]

    def execute() -> None:
        """Check the installer receives the Directory Manager secret in memory."""
        assert options.dirman_password == "directory-password-canary"
        if fail:
            raise RuntimeError("install failed")

    installer = Mock(return_value=SimpleNamespace(execute=execute))
    installer.option_parser.parse_args.side_effect = parse
    monkeypatch.setitem(
        sys.modules,
        "ipaserver.install.ipa_server_certinstall",
        SimpleNamespace(ServerCertInstall=installer),
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    previous_umask = os.umask(0o077)
    try:
        with pytest.raises(RuntimeError if fail else SystemExit) as caught:
            exec(command[-1], {})
    finally:
        os.umask(previous_umask)
    if not fail:
        assert caught.value.code == 0
    assert paths and all(not path.parent.exists() for path in paths)
    assert controller.run.call_args.args[0][-2:] == [
        "httpd.service",
        "dirsrv@HOME-ARPA.service",
    ]


def test_failed_refresh_is_retried_without_reissuing(
    controller: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The next Puppet invocation retries an unsuccessful service reload."""
    monkeypatch.setattr(sys, "argv", ["atlas-apply-certificates.py", "vault"])
    monkeypatch.setattr(
        controller,
        "Path",
        lambda _: SimpleNamespace(open=lambda _: nullcontext(object())),
    )
    monkeypatch.setattr(controller.fcntl, "flock", Mock())
    monkeypatch.setenv("OPENSSL_CONF", "before")
    action = Mock(side_effect=[RuntimeError("reload failed"), None])
    monkeypatch.setattr(controller, "apply", action)
    monkeypatch.setattr(controller, "check", Mock(return_value=True))
    with pytest.raises(RuntimeError, match="reload failed"):
        controller.main()
    controller.main()
    assert action.call_count == 2


def test_cli_surfaces_native_child_failure_without_private_input(
    tmp_path: Path,
) -> None:
    """Real child diagnostics reach Puppet without dumping its code or stdin secrets."""
    script = tmp_path / "proxmox/atlas-apply-certificates.py"
    script.parent.mkdir()
    script.write_text(
        (ROOT / "proxmox/atlas-apply-certificates.py")
        .read_text()
        .replace(
            "/run/lock/atlas-openvox-maintenance.lock",
            str(tmp_path / "maintenance.lock"),
        )
    )
    secrets = tmp_path / "docker/freeipa/secrets"
    secrets.mkdir(parents=True)
    for name, contents in (
        ("fullchain.pem", "public-certificate"),
        ("key.pem", "PRIVATE-KEY-CANARY"),
        ("installer.json", '{"dm_password":"PRIVATE-PASSWORD-CANARY"}'),
    ):
        (secrets / name).write_text(contents)
    binary = tmp_path / "bin/docker"
    binary.parent.mkdir()
    binary.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "request = json.load(sys.stdin)\n"
        "assert request['private_key'] == 'PRIVATE-KEY-CANARY'\n"
        "assert request['dm_password'] == 'PRIVATE-PASSWORD-CANARY'\n"
        "print('Native certificate import failed: unsupported key type', file=sys.stderr)\n"
        "raise SystemExit(7)\n"
    )
    binary.chmod(0o755)
    result = subprocess.run(
        [sys.executable, str(script), "ipa"],
        env={**os.environ, "PATH": f"{binary.parent}:{os.environ['PATH']}"},
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 7
    assert result.stdout == ""
    assert result.stderr == "Native certificate import failed: unsupported key type\n"
    assert "PRIVATE-KEY-CANARY" not in result.stderr
    assert "PRIVATE-PASSWORD-CANARY" not in result.stderr


@pytest.mark.parametrize(
    "service, ports",
    [("vault", ["8200"]), ("ipa", ["443", "636"]), ("openvox", ["8140"])],
)
def test_probe_rejects_missing_or_stale_listeners(
    controller: ModuleType, service: str, ports: list[str]
) -> None:
    """Puppet checks every service listener and retries on TLS mismatch or failure."""
    controller.run.side_effect = subprocess.CalledProcessError(1, "ruby")
    assert not controller.check(service)
    assert controller.run.call_args.args[0][-len(ports) :] == ports
    controller.run.side_effect = None
    assert controller.check(service)


@pytest.mark.parametrize("loaded", [False, True])
def test_openvox_native_renewal_restarts_only_a_stale_listener(
    controller: ModuleType, monkeypatch: pytest.MonkeyPatch, loaded: bool
) -> None:
    """Native renewal keeps the key and a reload failure can be retried without markers."""
    monkeypatch.setattr(controller, "check", Mock(return_value=loaded))
    controller.apply("openvox")
    calls = [call.args[0] for call in controller.run.call_args_list]
    assert calls[0] == controller.DOCKER + [
        "exec",
        "infra-openvox-1",
        "/opt/puppetlabs/bin/puppet",
        "ssl",
        "renew_cert",
        "--if-expiring-in",
        "30d",
        "--config",
        "/etc/puppetlabs/puppet/puppet.conf",
    ]
    assert len(calls) == (1 if loaded else 2)
    if not loaded:
        assert calls[1] == controller.DOCKER + [
            "restart",
            "--time",
            "60",
            "infra-openvox-1",
        ]
    controller.check.assert_called_once_with("openvox")


def test_failed_native_renewal_does_not_restart_server(
    controller: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rejected native renewal leaves the running certificate and server intact."""
    controller.run.side_effect = subprocess.CalledProcessError(1, "puppet")
    monkeypatch.setattr(controller, "check", Mock())
    with pytest.raises(subprocess.CalledProcessError):
        controller.apply("openvox")
    assert controller.run.call_count == 1
    controller.check.assert_not_called()


def test_openvox_probe_uses_native_store_and_checks_renewal_window(
    controller: ModuleType,
) -> None:
    """Puppet's guard checks expiry without requiring certificate bundles or markers."""
    assert controller.check("openvox", renewal_due=True)
    command = controller.run.call_args.args[0]
    assert command[:7] == controller.DOCKER + [
        "exec",
        "infra-openvox-1",
        controller.RUBY,
        "-e",
    ]
    assert command[-5:] == [
        "/etc/puppetlabs/puppet/ssl/certs/openvox.home.arpa.pem",
        "/trust/root.pem",
        "openvox.home.arpa",
        "30",
        "8140",
    ]


def test_native_mldsa_probe_checks_live_leaf_and_trust(
    controller: ModuleType, tmp_path: Path
) -> None:
    """The actual TLS probe rejects a stale leaf and an unauthenticated listener."""
    controller.check("vault")
    worker = controller.run.call_args.args[0][2]
    program = r"""
require 'openssl'; require 'socket'
def issue(name, issuer=nil, issuer_key=nil)
  key = OpenSSL::PKey.generate_key('ML-DSA-44')
  cert = OpenSSL::X509::Certificate.new
  cert.version = 2; cert.serial = rand(1_000_000)
  cert.subject = OpenSSL::X509::Name.parse("/CN=#{name}")
  cert.issuer = issuer ? issuer.subject : cert.subject
  cert.public_key = key
  cert.not_before = Time.now - 60; cert.not_after = Time.now + 86400
  factory = OpenSSL::X509::ExtensionFactory.new
  factory.subject_certificate = cert; factory.issuer_certificate = issuer || cert
  cert.add_extension(factory.create_extension('basicConstraints', issuer ? 'CA:FALSE' : 'CA:TRUE', true))
  cert.add_extension(factory.create_extension('subjectAltName', "DNS:#{name}")) if issuer
  cert.sign(issuer_key || key, nil)
  [cert, key]
end
root, root_key = issue('Root')
other_root, = issue('Other Root')
leaf, key = issue('vault.home.arpa', root, root_key)
directory, worker = ARGV
File.write(directory + '/root.pem', root.to_pem)
File.write(directory + '/other.pem', other_root.to_pem)
File.write(directory + '/leaf.pem', leaf.to_pem)
stale = OpenSSL::X509::Certificate.new(leaf.to_der)
stale.serial += 1; stale.sign(root_key, nil)
File.write(directory + '/stale.pem', stale.to_pem)
server = TCPServer.new('127.0.0.1', 0)
context = OpenSSL::SSL::SSLContext.new
context.cert = leaf; context.key = key; context.extra_chain_cert = [root]
listener = OpenSSL::SSL::SSLServer.new(server, context)
thread = Thread.new do
  4.times do
    begin; tls = listener.accept; tls.close; rescue OpenSSL::SSL::SSLError; end
  end
end
[['leaf', 'root', true], ['stale', 'root', false], ['leaf', 'other', false]].each do |cert, ca, expected|
  system(RbConfig.ruby, '-e', worker, directory + '/' + cert + '.pem', directory + '/' + ca + '.pem', 'vault.home.arpa', '0', server.addr[1].to_s, out: File::NULL, err: File::NULL)
  raise 'Incorrect TLS probe result' unless $?.success? == expected
end
system(RbConfig.ruby, '-e', worker, directory + '/leaf.pem', directory + '/root.pem', 'vault.home.arpa', '30', server.addr[1].to_s, out: File::NULL, err: File::NULL)
raise 'Expiring certificate must request native renewal' if $?.success?
system(RbConfig.ruby, '-e', worker, directory + '/leaf.pem', directory + '/root.pem', 'vault.home.arpa', '0', server.addr[1].to_s, out: File::NULL, err: File::NULL)
raise 'Same live certificate must pass without renewal guard' unless $?.success?
thread.join; server.close
"""
    result = subprocess.run(
        [controller.RUBY, "-e", program, str(tmp_path), worker],
        env={**os.environ, "OPENSSL_CONF": str(ROOT / "docker/openvox/openssl.cnf")},
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
