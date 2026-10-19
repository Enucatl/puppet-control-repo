"""Exercise the CRL publication gate with original, in-memory ML-DSA fixtures."""

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
FIXTURE = r"""
require 'openssl'
source = STDIN.read
scenario = ARGV.fetch(0)
key = OpenSSL::PKey.generate_key('ML-DSA-44')
issuer = OpenSSL::X509::Certificate.new
issuer.version = 2
issuer.serial = 1
issuer.subject = issuer.issuer = OpenSSL::X509::Name.parse('/CN=Publisher_test_CA')
issuer.public_key = key
issuer.not_before = Time.now - 3600
issuer.not_after = Time.now + 3600
issuer.sign(key, nil)
crl = OpenSSL::X509::CRL.new
crl.version = 1
crl.issuer = issuer.subject
crl.last_update = Time.now - 120
crl.next_update = Time.now + 3600 unless scenario == 'missing_next_update'
crl.last_update = Time.now + 60 if scenario == 'future_last_update'
crl.next_update = Time.now - 60 if scenario == 'expired'
crl.issuer = OpenSSL::X509::Name.parse('/CN=Other_issuer') if scenario == 'wrong_issuer'
signer = scenario == 'wrong_signer' ? OpenSSL::PKey.generate_key('ML-DSA-44') : key
if scenario == 'classical_signature'
  crl.sign(OpenSSL::PKey::RSA.new(2048), OpenSSL::Digest.new('SHA256'))
else
  crl.sign(signer, nil)
end
if scenario == 'tampered_signature'
  der = crl.to_der
  der.setbyte(der.bytesize - 1, der.getbyte(der.bytesize - 1) ^ 1)
  crl = OpenSSL::X509::CRL.new(der)
end
pem = scenario == 'empty_input' ? '' : crl.to_pem
issuer_bundle = issuer.to_pem
if scenario == 'valid_bundle'
  other_key = OpenSSL::PKey.generate_key('ML-DSA-44')
  other_issuer = OpenSSL::X509::Certificate.new(issuer.to_der)
  other_issuer.subject = other_issuer.issuer = OpenSSL::X509::Name.parse('/CN=Other_bundle_issuer')
  other_issuer.public_key = other_key
  other_issuer.sign(other_key, nil)
  other_crl = OpenSSL::X509::CRL.new(crl.to_der)
  other_crl.issuer = other_issuer.subject
  other_crl.sign(other_key, nil)
  pem += other_crl.to_pem
  issuer_bundle += other_issuer.to_pem
end
read_file = File.method(:read)
File.define_singleton_method(:read) do |path, *args|
  case path
  when '/memory/crl.pem' then pem
  when '/memory/issuer.pem' then issuer_bundle
  else read_file.call(path, *args)
  end
end
ARGV.replace(['--crl', '/memory/crl.pem', '--issuer', '/memory/issuer.pem'])
eval(source, TOPLEVEL_BINDING, 'verify-crypto.rb')
"""


@pytest.fixture(scope="module", autouse=True)
def require_mldsa_ruby() -> None:
    """Use the accepted agent's Ruby, skipping hosts without ML-DSA support."""
    if RUBY is None:
        pytest.skip("Ruby is unavailable")
    probe = subprocess.run(
        [RUBY, "-ropenssl", "-e", "OpenSSL::PKey.generate_key('ML-DSA-44')"],
        capture_output=True,
        check=False,
    )
    if probe.returncode:
        pytest.skip("Ruby/OpenSSL does not support ML-DSA-44")


@pytest.mark.parametrize(
    ("scenario", "accepted"),
    [
        ("valid", True),
        ("valid_bundle", True),
        ("tampered_signature", False),
        ("wrong_signer", False),
        ("wrong_issuer", False),
        ("expired", False),
        ("future_last_update", False),
        ("missing_next_update", False),
        ("classical_signature", False),
        ("empty_input", False),
    ],
)
def test_crl_publisher_validation(scenario: str, accepted: bool) -> None:
    """Require issuer, signature, algorithm and validity before CRL publication."""
    result = subprocess.run(
        [RUBY, "-e", FIXTURE, scenario],
        input=(ROOT / "docker/openvox/verify-crypto.rb").read_text(),
        text=True,
        capture_output=True,
        check=False,
    )
    assert (result.returncode == 0) is accepted, result.stdout + result.stderr
    assert "PASS crl" in result.stdout if accepted else "FAIL crl" in result.stderr
