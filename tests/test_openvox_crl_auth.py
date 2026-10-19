"""Exercise the CA authorization patch while retaining package defaults."""

import json
import os
from pathlib import Path
import subprocess

import pytest

from test_openvox_crl_setup import RUBY


ROOT = Path(__file__).resolve().parents[1]
AUTH = """
authorization: {
  version: 1
  rules: [
    {
      name: "CA administrators"
      match-request: { path: "/puppet-ca/v1/certificate_status", type: path, method: put }
      allow: { extensions: { pp_cli_auth: "true" } }
      sort-order: 500
    },
    {
      name: "deny unmatched requests"
      match-request: { path: "/", type: path }
      deny: "*"
      sort-order: 999
    }
  ]
}
other-setting = ${OPENVOX_OTHER_TEST_VALUE}
"""


def test_narrow_publisher_rule_preserves_defaults_and_environment(
    tmp_path: Path,
) -> None:
    """Keep existing rules and unresolved runtime substitutions across repeated builds."""
    if RUBY is None:
        pytest.skip("Ruby is unavailable")
    probe = subprocess.run(
        [RUBY, "-rhocon/config_factory", "-e", ""], capture_output=True
    )
    if probe.returncode:
        pytest.skip("The package's HOCON gem is unavailable")
    path = tmp_path / "auth.conf"
    path.write_text(AUTH)
    for _ in range(2):
        result = subprocess.run(
            [RUBY, str(ROOT / "docker/openvox/infra-crl-auth.rb"), str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
    environment = {
        **os.environ,
        "OPENVOX_CRL_PUBLISHER_CERTNAME": "publisher.example",
        "OPENVOX_OTHER_TEST_VALUE": "preserved",
    }
    result = subprocess.run(
        [
            RUBY,
            "-rhocon/config_factory",
            "-rjson",
            "-e",
            "puts JSON.generate(Hocon::ConfigFactory.parse_file(ARGV[0]).resolve.root.unwrapped)",
            str(path),
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    rules = config["authorization"]["rules"]
    assert len(rules) == 3
    assert rules[0] == {
        "name": "OpenVox CRL publisher",
        "match-request": {
            "path": "^/puppet-ca/v1/certificate_revocation_list$",
            "type": "regex",
            "method": "put",
        },
        "allow": ["publisher.example", {"extensions": {"pp_cli_auth": "true"}}],
        "sort-order": 499,
    }
    assert rules[1]["allow"] == {"extensions": {"pp_cli_auth": "true"}}
    assert rules[2]["deny"] == "*"
    assert config["other-setting"] == "preserved"
    assert "${OPENVOX_CRL_PUBLISHER_CERTNAME}" in path.read_text()
