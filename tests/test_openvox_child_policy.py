"""Keep strict native TLS inside Puppet while subprocesses use system policy."""

import json
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RUBY = "/opt/puppetlabs/puppet/bin/ruby"


def test_native_openssl_remains_strict_after_child_environment_changes(
    tmp_path: Path,
) -> None:
    """Check real parent and child SSL contexts with the accepted Ruby/OpenSSL."""
    shim = tmp_path / "child-policy.rb"
    shim.write_text(
        (ROOT / "modules/profile/files/openvox-child-policy.rb")
        .read_text()
        .replace(
            "/etc/puppetlabs/puppet/openssl.cnf",
            str(ROOT / "docker/openvox/openssl.cnf"),
        )
    )
    program = """
require 'json'
require 'open3'
parent = OpenSSL::SSL::SSLContext.new.options & OpenSSL::SSL::OP_NO_TLSv1_2
child, status = Open3.capture2(RbConfig.ruby, '-ropenssl', '-e',
  'puts OpenSSL::SSL::SSLContext.new.options & OpenSSL::SSL::OP_NO_TLSv1_2')
abort 'Child failed' unless status.success?
puts JSON.generate(parent_disables_tls12: parent != 0,
  child_disables_tls12: child.to_i != 0, child_config: ENV['OPENSSL_CONF'])
"""
    result = json.loads(
        subprocess.check_output(
            [RUBY, "-r", str(shim), "-e", program],
            text=True,
            env={**os.environ, "OPENSSL_CONF": "/dev/null"},
        )
    )
    assert result == {
        "parent_disables_tls12": True,
        "child_disables_tls12": False,
        "child_config": "/etc/ssl/openssl.cnf",
    }
