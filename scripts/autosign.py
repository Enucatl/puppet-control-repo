#!/opt/puppet-user/venv/bin/python

import os

import click
import hvac
import cryptography.x509


@click.command()
@click.argument("certname")
@click.argument("input_file", type=click.File("rb"), default="-")
@click.option(
    "--vault_addr", default=os.environ.get("VAULT_ADDR", "https://vault.home.arpa:8200")
)
@click.option("--policy", default="puppet")
@click.option("--verify", default="/etc/ssl/certs/puppet_ca.pem")
def main(certname, input_file, vault_addr, policy, verify):
    """
    Check the challengePassword OID in a Certificate Signing Request (CSR)
    to verify if it's a valid token for HashiCorp Vault login.

    To be used as an autosign policy executable:
    https://www.puppet.com/docs/puppet/8/ssl_autosign#ssl_policy_based_autosigning-custom-policy-executables

    Parameters:
    - certname (str): Name or identifier of the certificate.
    - input_file (file): Input file containing PEM-encoded CSR data.
    - vault_addr (str, optional): HashiCorp Vault server address.
    - policy (str, optional): Policy name to check in HashiCorp Vault.
    - verify (str, optional): Location of the root CA certificate for SSL.

    Raises:
    - cryptography.x509.base.AttributeNotFound: If 'challengePassword' attribute is not found.
    - PermissionError: If the token lacks the specified policy in HashiCorp Vault.
    - hvac.exceptions.InvalidRequest: If the token can't authenticate with Vault.

    Procedure:
    1. Read CSR data from the input file.
    2. Load CSR data into a cryptography.x509.CertificateSigningRequest object.
    3. Define ChallengePassword OID as '1.2.840.113549.1.9.7'.
    4. Retrieve ChallengePassword attribute from CSR using the OID.
    5. Decode the attribute value into a token.
    6. Initialize HashiCorp Vault client with Vault address and token from the attribute.
    7. Check if the token can authenticate with HashiCorp Vault.
    8. Get the list of policies associated with the token.
    9. Check if the specified policy is in the list of policies; if not, raise a PermissionError.

    Example Usage:
    ```
    $ python script.py mycertname csr.pem --vault_addr="https://vault.example.com" --policy="my_policy"
    ```
    """

    _ = certname
    csr_data = input_file.read()
    csr = cryptography.x509.load_pem_x509_csr(csr_data)

    # challengePassword OID
    dotted_string = "1.2.840.113549.1.9.7"

    # Create an ObjectIdentifier for the ChallengePassword OID
    oid = cryptography.x509.ObjectIdentifier(dotted_string)

    # will raise cryptography.x509.base.AttributeNotFound if not found
    attribute = csr.attributes.get_attribute_for_oid(oid)
    token = attribute.value.decode()
    vault_client = hvac.Client(
        url=vault_addr,
        token=token,
        verify=verify,
    )
    # will raise if the token cannot authenticate itself
    current_token = vault_client.auth.token.lookup_self()
    policies = current_token["data"]["policies"]
    if policy not in policies:
        raise PermissionError(f"token does not have the policy {policy}: {policies}")


if __name__ == "__main__":
    main()
