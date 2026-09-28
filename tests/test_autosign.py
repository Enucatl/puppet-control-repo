"""CSR identity and fail-closed enrollment validation."""

import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest
from click.testing import CliRunner
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

SPEC = importlib.util.spec_from_file_location(
    "autosign", Path(__file__).parents[1] / "scripts/autosign.py"
)
autosign = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(autosign)
CERTNAME = "test.home.arpa"
TOKEN = "test-secret-enrollment-token"


def make_csr(
    names: tuple[str, ...] = (CERTNAME,),
    sans: list[x509.GeneralName] | None = None,
    token: bytes | None = TOKEN.encode(),
) -> bytes:
    """Generate a real signed CSR for the requested test identities."""
    builder = x509.CertificateSigningRequestBuilder().subject_name(
        x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name) for name in names])
    )
    if sans is not None:
        builder = builder.add_extension(
            x509.SubjectAlternativeName(sans), critical=False
        )
    if token is not None:
        builder = builder.add_attribute(
            x509.ObjectIdentifier("1.2.840.113549.1.9.7"), token
        )
    return builder.sign(
        ec.generate_private_key(ec.SECP256R1()), hashes.SHA256()
    ).public_bytes(serialization.Encoding.PEM)


@pytest.fixture
def vault(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Replace only the Vault transport while exercising real CSR parsing."""
    client = Mock()
    client.return_value.auth.token.lookup_self.return_value = {
        "data": {"policies": ["puppet-enrollment"], "meta": {"certname": CERTNAME}}
    }
    monkeypatch.setattr(autosign.hvac, "Client", client)
    return client


def invoke(csr: bytes, certname: str = CERTNAME):
    """Invoke the actual executable entry point with a CSR on stdin."""
    return CliRunner().invoke(autosign.main, [certname], input=csr)


@pytest.mark.parametrize("certname", [CERTNAME, "node-2.dev.home.arpa", "a.home.arpa"])
@pytest.mark.parametrize("with_san", [False, True])
def test_matching_csr_uses_exactly_one_lookup(
    vault: Mock, certname: str, with_san: bool
) -> None:
    """Both supported namespaces accept only the matching enrollment identity."""
    vault.return_value.auth.token.lookup_self.return_value["data"]["meta"][
        "certname"
    ] = certname
    result = invoke(
        make_csr((certname,), [x509.DNSName(certname)] if with_san else None), certname
    )
    assert result.exit_code == 0, result.output
    vault.assert_called_once_with(
        url="https://hcv.home.arpa:8200",
        token=TOKEN,
        verify="/etc/ssl/certs/ca-certificates.crt",
        timeout=10,
        allow_redirects=False,
    )
    vault.return_value.auth.token.lookup_self.assert_called_once_with()
    assert len(vault.return_value.mock_calls) == 1


@pytest.mark.parametrize(
    "certname",
    [
        "test.home.arpa\n",
        "test.home.arpa.evil",
        "UPPER.home.arpa",
        "-test.home.arpa",
        "test.prod.home.arpa",
    ],
)
def test_invalid_hostname_never_contacts_vault(vault: Mock, certname: str) -> None:
    """Hostname matching covers the entire requested certname."""
    assert invoke(make_csr(), certname).exit_code != 0
    vault.assert_not_called()


@pytest.mark.parametrize(
    ("names", "sans", "token"),
    [
        (("other.home.arpa",), None, TOKEN.encode()),
        ((), None, TOKEN.encode()),
        ((CERTNAME, "other.home.arpa"), None, TOKEN.encode()),
        ((CERTNAME,), [x509.DNSName("other.home.arpa")], TOKEN.encode()),
        (
            (CERTNAME,),
            [x509.DNSName(CERTNAME), x509.DNSName("other.home.arpa")],
            TOKEN.encode(),
        ),
        (
            (CERTNAME,),
            [x509.UniformResourceIdentifier("https://other.home.arpa")],
            TOKEN.encode(),
        ),
        ((CERTNAME,), None, None),
    ],
)
def test_invalid_csr_never_contacts_vault(
    vault: Mock, names: tuple[str, ...], sans: list | None, token: bytes | None
) -> None:
    """Reject name ambiguity, extra identities, and missing credentials locally."""
    result = invoke(make_csr(names, sans, token))
    assert result.exit_code != 0
    assert TOKEN not in result.output
    vault.assert_not_called()


def test_invalid_signature_and_pem(vault: Mock) -> None:
    """Reject malformed requests and a CSR with a tampered signature."""
    csr = x509.load_pem_x509_csr(make_csr()).public_bytes(serialization.Encoding.DER)
    altered = x509.load_der_x509_csr(csr[:-1] + bytes([csr[-1] ^ 1])).public_bytes(
        serialization.Encoding.PEM
    )
    for invalid in (b"not a CSR", altered):
        assert invoke(invalid).exit_code != 0
    vault.assert_not_called()


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"policies": ["puppet-enrollment"]},
        {"policies": ["puppet-enrollment"], "meta": None},
        {"policies": ["puppet-enrollment"], "meta": {}},
        {"policies": ["puppet-enrollment"], "meta": {"certname": "other.home.arpa"}},
        {"policies": ["puppet"], "meta": {"certname": CERTNAME}},
        {"policies": ["puppet", "puppet-enrollment"], "meta": {"certname": CERTNAME}},
        {"policies": [], "meta": {"certname": CERTNAME}},
    ],
)
def test_unbound_or_runtime_token_denied(vault: Mock, data: dict) -> None:
    """Only the enrollment-only policy with exact metadata authorizes signing."""
    vault.return_value.auth.token.lookup_self.return_value = {"data": data}
    assert invoke(make_csr()).exit_code != 0
    vault.return_value.auth.token.lookup_self.assert_called_once_with()


@pytest.mark.parametrize(
    "error",
    [
        PermissionError(TOKEN),
        autosign.hvac.exceptions.Forbidden(TOKEN),
        TimeoutError(TOKEN),
        ValueError(TOKEN),
    ],
)
def test_vault_errors_are_denied_without_retry_or_secret_output(
    vault: Mock, error: Exception
) -> None:
    """Transport and authentication failures produce only a generic denial."""
    vault.return_value.auth.token.lookup_self.side_effect = error
    result = invoke(make_csr())
    assert result.exit_code != 0
    assert result.output == "Error: Puppet enrollment denied\n"
    vault.return_value.auth.token.lookup_self.assert_called_once_with()
