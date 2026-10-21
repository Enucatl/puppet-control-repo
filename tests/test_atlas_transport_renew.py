"""Exercise transport renewal retries without issuing or changing live credentials."""

from __future__ import annotations

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
    """Load renewal with disposable recovery state and inert external boundaries."""
    spec = importlib.util.spec_from_file_location(
        "renew", ROOT / "proxmox/atlas-transport-renew.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.infra, "STATE", tmp_path)
    monkeypatch.setattr(module.infra, "run", Mock(return_value=""))
    monkeypatch.setattr(module, "api", Mock())
    monkeypatch.setattr(
        module,
        "certificate",
        Mock(
            return_value={
                "due": True,
                "csr": "CSR",
                "not_after": "later",
                "public_key": "PIN",
            }
        ),
    )
    return module


@pytest.mark.parametrize("service", ["vault", "http", "ldap"])
def test_interrupted_install_reuses_issued_identity_and_recovery_inputs(
    controller: ModuleType, service: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure after recovery publication preserves a retry without another issuance."""
    state = controller.infra.STATE
    (state / "freeipa").mkdir()
    (state / "freeipa/installer.json").write_text(
        json.dumps({"dm_password": "protected-password-canary"})
    )
    path = (
        state / "vault-transport.json"
        if service == "vault"
        else state / "freeipa" / f"{service}.json"
    )
    old = {
        "certificate": "old",
        "private_key": "same key",
        "ca_chain": ["issuer", "root"],
    }
    issued = {**old, "certificate": "renewed"}
    path.write_text(json.dumps(old))
    controller.api.side_effect = [
        {"auth": {"client_token": "short-lived"}},
        {"data": issued},
        {},
    ]
    controller.certificate.side_effect = [
        {"due": True, "csr": "CSR"},
        {"due": False},
        {"due": False, "not_after": "later", "public_key": "PIN"},
    ]
    monkeypatch.setattr(controller.infra, "volume_file", Mock())
    controller.infra.run.side_effect = RuntimeError("reload failed")
    with pytest.raises(RuntimeError, match="reload failed"):
        controller.renew(service)
    pending = path.with_suffix(".renewal-pending.json")
    assert pending.exists()
    assert json.loads(path.read_text()) == issued
    if service != "vault":
        assert (state / "freeipa" / f"{service}-key.pem").read_text() == "same key"
        assert not (state / "freeipa/container-inputs").exists()
    count = controller.api.call_count
    controller.infra.run.side_effect = None
    controller.certificate.side_effect = None
    controller.certificate.return_value = {
        "due": False,
        "not_after": "later",
        "public_key": "PIN",
    }
    controller.renew(service)
    assert not pending.exists()
    assert controller.api.call_count == count
    if service == "vault":
        assert controller.infra.volume_file.call_args_list[-2].args == (
            "certificates",
            "public/vault.pub.pem",
            "PIN",
        )
    else:
        worker = controller.infra.run.call_args_list[-2]
        assert "protected-password-canary" not in str(worker.args[0])
        assert "same key" not in str(worker.args[0])
        assert json.loads(worker.args[1])["dm_password"] == "protected-password-canary"
        assert 'options.dirman_password = request["dm_password"]' in worker.args[0][-1]


@pytest.mark.parametrize("service", ["http", "ldap"])
@pytest.mark.parametrize("fail", [False, True])
def test_freeipa_worker_protects_and_cleans_temporary_credentials(
    controller: ModuleType,
    service: str,
    fail: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Native certinstall receives private stdin inputs and leaves no temporary key."""
    state = controller.infra.STATE / "freeipa"
    state.mkdir()
    (state / "installer.json").write_text(
        json.dumps({"dm_password": "directory secret"})
    )
    controller.install(
        {
            "certificate": "leaf",
            "ca_chain": ["root"],
            "private_key": "private-key-canary-value",
        },
        service,
        "public",
    )
    command, payload = controller.infra.run.call_args_list[0].args
    assert "directory secret" not in str(command)
    assert "private-key-canary-value" not in str(command)
    options = SimpleNamespace()
    paths = []

    def parse(arguments: list[str]) -> tuple[SimpleNamespace, list[str]]:
        """Inspect the files at the native install boundary."""
        assert arguments[0] == ("--http" if service == "http" else "--dirsrv")
        paths.extend(Path(path) for path in arguments[2:])
        assert [path.read_text() for path in paths] == [
            "leaf\nroot\n",
            "private-key-canary-value",
        ]
        assert all(path.stat().st_mode & 0o777 == 0o600 for path in paths)
        assert paths[0].parent.stat().st_mode & 0o777 == 0o700
        return options, arguments[2:]

    def execute() -> None:
        """Check the secret reaches the installer without a command argument."""
        assert options.dirman_password == "directory secret"
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


def test_noop_never_authenticates_or_reloads(controller: ModuleType) -> None:
    """Regular checks verify the endpoint while leaving healthy service state alone."""
    (controller.infra.STATE / "vault-transport.json").write_text("{}")
    controller.certificate.return_value = {"due": False, "not_after": "later"}
    controller.renew("vault")
    controller.api.assert_not_called()
    controller.infra.run.assert_not_called()
    assert controller.certificate.call_args.kwargs == {"live": True}


def test_install_keeps_existing_bootstrap_inputs_current(
    controller: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Established wrapper startup still finds the same keys as recovery state."""
    state = controller.infra.STATE / "freeipa"
    (state / "container-inputs").mkdir(parents=True)
    (state / "installer.json").write_text('{"dm_password":"fixture"}')
    monkeypatch.setattr(controller.os, "chown", Mock())
    controller.install(
        {"certificate": "leaf", "ca_chain": ["issuer", "root"], "private_key": "key"},
        "http",
        "public",
    )
    for name in ("http-chain.pem", "http-key.pem"):
        assert (state / "container-inputs" / name).read_bytes() == (
            state / name
        ).read_bytes()
    assert controller.os.chown.call_count == 2


def test_configure_scopes_agent_auth_to_transport_signing(
    controller: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only explicit setup consumes authority credentials; renewal cannot alter roles."""
    privileged = Mock()
    monkeypatch.setattr(controller.infra, "api", privileged)
    monkeypatch.setattr(controller.infra, "openvox", Mock(return_value="CHAIN"))
    controller.configure()
    policy = privileged.call_args_list[0].args[2]["policy"]
    assert "pki_int/sign/vault-transport" in policy
    assert "pki_freeipa/sign/ipa-transport" in policy
    assert "*" not in policy and "issue/" not in policy
    role = privileged.call_args_list[1].args[2]
    assert role["allowed_common_names"] == ["atlas.home.arpa"]
    assert role["token_max_ttl"] == "5m"
    assert role["token_no_default_policy"] is True


def test_native_mldsa_csr_and_renewal_validation(tmp_path: Path) -> None:
    """Real native crypto rejects changed keys, identities and root fingerprints."""
    program = r"""
require 'tmpdir'
require ARGV.fetch(0)
def issue(name, issuer=nil, issuer_key=nil)
  key = OpenSSL::PKey.generate_key('ML-DSA-44')
  cert = OpenSSL::X509::Certificate.new
  cert.version = 2; cert.serial = rand(1_000_000)
  cert.subject = OpenSSL::X509::Name.parse("/CN=#{name}")
  cert.issuer = issuer ? issuer.subject : cert.subject
  cert.public_key = key
  cert.not_before = Time.now - 60
  cert.not_after = Time.now + (issuer ? 90 : 365) * 86400
  factory = OpenSSL::X509::ExtensionFactory.new
  factory.subject_certificate = cert; factory.issuer_certificate = issuer || cert
  cert.add_extension(factory.create_extension('basicConstraints', issuer_key && name.include?('.') ? 'CA:FALSE' : 'CA:TRUE', true))
  cert.add_extension(factory.create_extension('subjectAltName', "DNS:#{name}")) if name.include?('.')
  cert.sign(issuer_key || key, nil)
  [cert, key]
end
root, root_key = issue('Root')
issuer, issuer_key = issue('Issuer', root, root_key)
leaf, key = issue('vault.home.arpa', issuer, issuer_key)
File.write(ARGV.fetch(1), root.to_pem)
data = {'certificate'=>leaf.to_pem, 'private_key'=>key.private_to_pem, 'ca_chain'=>[issuer.to_pem, root.to_pem]}
request = {'data'=>data, 'root'=>ARGV.fetch(1), 'root_sha256'=>Digest::SHA256.hexdigest(root.to_der), 'hostname'=>'vault.home.arpa', 'window_days'=>10}
result = TransportCertificate.run(request)
csr = OpenSSL::X509::Request.new(result.fetch(:csr))
raise 'CSR invalid' unless csr.verify(key) && csr.subject.to_der == leaf.subject.to_der
raise 'Lost SAN' unless csr.attributes.any? { |a| a.oid == 'extReq' }
[request.merge('root_sha256'=>'wrong'), request.merge('hostname'=>'wrong.home.arpa'),
 request.merge('data'=>data.merge('private_key'=>root_key.private_to_pem))].each do |bad|
  rejected = false
  begin; TransportCertificate.run(bad); rescue RuntimeError; rejected = true; end
  raise 'Accepted a changed identity' unless rejected
end
server = TCPServer.new('127.0.0.1', 0)
context = OpenSSL::SSL::SSLContext.new
context.cert = leaf; context.key = key; context.extra_chain_cert = [issuer]
context.min_version = context.max_version = OpenSSL::SSL::TLS1_3_VERSION
listener = OpenSSL::SSL::SSLServer.new(server, context)
worker = Thread.new do
  2.times do
    socket = listener.accept
    socket.close
  end
end
TransportCertificate.run(request.merge('port'=>server.addr[1]))
other = OpenSSL::X509::Certificate.new(leaf.to_der)
other.serial += 1; other.sign(issuer_key, nil)
rejected = false
begin
  TransportCertificate.run(request.merge('data'=>data.merge('certificate'=>other.to_pem), 'port'=>server.addr[1]))
rescue RuntimeError
  rejected = true
end
raise 'Accepted stale listener' unless rejected
worker.join; server.close
puts 'Native transport crypto checks passed'
"""
    environment = {
        **os.environ,
        "OPENSSL_CONF": str(ROOT / "docker/openvox/openssl.cnf"),
    }
    result = subprocess.run(
        [
            "/opt/puppetlabs/puppet/bin/ruby",
            "-e",
            program,
            str(ROOT / "docker/openvox/infra-transport-cert.rb"),
            str(tmp_path / "root.pem"),
        ],
        text=True,
        capture_output=True,
        env=environment,
    )
    assert result.returncode == 0, result.stderr


def test_runtime_auth_never_reads_bootstrap_token(controller: ModuleType) -> None:
    """The shared TLS client gets only explicit agent credentials and in-memory token."""
    spec = importlib.util.spec_from_file_location(
        "runtime_auth", ROOT / "proxmox/atlas-transport-renew.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.infra.STATE = controller.infra.STATE
    module.infra.run = Mock(return_value='{"status":200,"body":{}}')
    module.api("auth/cert/login", {"name": "atlas-transport-renew"})
    login = json.loads(module.infra.run.call_args.args[1])
    assert "token_file" not in login and login["auth"] is False
    assert login["cert_file"].endswith("/certs/atlas.home.arpa.pem")
    assert login["key_file"].endswith("/private_keys/atlas.home.arpa.pem")
    module.api("pki_int/sign/vault-transport", {"csr": "CSR"}, "ephemeral")
    signing = json.loads(module.infra.run.call_args.args[1])
    assert "token_file" not in signing and signing["token"] == "ephemeral"
