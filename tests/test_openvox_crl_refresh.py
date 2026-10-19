"""Exercise the fleet CRL updater with real ML-DSA certificate chains."""

from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUBY = (
    "/opt/puppetlabs/puppet/bin/ruby"
    if Path("/opt/puppetlabs/puppet/bin/ruby").is_file()
    else shutil.which("ruby")
)
SCENARIOS = (
    "valid revoked_intermediate expired future tampered missing duplicate conflict "
    "older_number older_timestamp online_ttl issuer_lifetime rollback failed_fetch "
    "running_consumer healthy stale near_expiry future_status failed_status "
    "publisher publisher_idempotent publisher_failure publisher_revoked lock_held "
    "cached_fallback expired_cache missing_cache failed_output "
    "root_failure root_tampered root_rollback sync_outage "
    "login_http login_blank_credentials login_missing_file login_blank_token "
    "login_tls_valid login_tls_revoked_peer login_tls_revoked_issuer "
    "login_tls_missing_crl login_tls_wrong_hostname malicious_issuer cached_revoked"
).split()


@pytest.fixture(scope="module", autouse=True)
def require_mldsa_ruby() -> None:
    """Skip on old host Ruby; run the same fixture in the accepted container."""
    if RUBY is None:
        pytest.skip("Ruby is unavailable")
    probe = subprocess.run(
        [RUBY, "-ropenssl", "-e", "OpenSSL::PKey.generate_key('ML-DSA-44')"],
        capture_output=True,
        check=False,
    )
    if probe.returncode:
        pytest.skip("Ruby/OpenSSL does not support ML-DSA-44")


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_crl_refresh(scenario: str) -> None:
    """Validate signatures, freshness, rollback protection and consumer updates."""
    result = subprocess.run(
        [
            RUBY,
            str(ROOT / "tests/fixtures/openvox_crl_refresh.rb"),
            str(ROOT / "modules/profile/files/openvox/crl-refresh.rb"),
            scenario,
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"PASS {scenario}" in result.stdout
