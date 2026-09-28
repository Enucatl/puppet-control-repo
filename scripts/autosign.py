#!/opt/puppetlabs/puppet/venv/bin/python

import os
import re
from typing import BinaryIO

import click
import hvac
from cryptography import x509
from cryptography.x509.oid import NameOID


def validate_csr(
    certname: str, csr_data: bytes, vault_addr: str, verify: str, domain: str
) -> None:
    """Consume a certname-bound enrollment token to authorize a signed CSR."""
    if not re.fullmatch(
        rf"[a-z0-9]([a-z0-9-]*[a-z0-9])?\.(dev\.)?{re.escape(domain)}",
        certname,
    ):
        raise ValueError("Invalid certname")

    csr = x509.load_pem_x509_csr(csr_data)
    names = csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    if not csr.is_signature_valid or len(names) != 1 or names[0].value != certname:
        raise ValueError("CSR common name or signature does not match")
    try:
        sans = csr.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        sans = []
    if any(
        not isinstance(name, x509.DNSName) or name.value != certname for name in sans
    ):
        raise ValueError("CSR requests additional identities")

    token = csr.attributes.get_attribute_for_oid(
        x509.ObjectIdentifier("1.2.840.113549.1.9.7")
    ).value.decode()
    if not token:
        raise ValueError("Missing enrollment token")
    client = hvac.Client(
        url=vault_addr, token=token, verify=verify, timeout=10, allow_redirects=False
    )
    # This single request consumes the token, including on a metadata mismatch.
    data = client.auth.token.lookup_self()["data"]
    if (
        data["policies"] != ["puppet-enrollment"]
        or data["meta"]["certname"] != certname
    ):
        raise PermissionError("Enrollment policy or certname does not match")


@click.command()
@click.argument("certname")
@click.argument("input_file", type=click.File("rb"), default="-")
@click.option(
    "--vault_addr", default=os.environ.get("VAULT_ADDR", "https://hcv.home.arpa:8200")
)
@click.option("--verify", default="/etc/ssl/certs/ca-certificates.crt")
@click.option("--domain", default="home.arpa")
def main(
    certname: str, input_file: BinaryIO, vault_addr: str, verify: str, domain: str
) -> None:
    """Approve enrollment only for a matching single-use Vault credential."""
    try:
        validate_csr(certname, input_file.read(), vault_addr, verify, domain)
    except Exception:
        # Vault errors can contain credentials; never include their text in logs.
        raise click.ClickException("Puppet enrollment denied") from None


if __name__ == "__main__":
    main()
