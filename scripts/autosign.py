#!/opt/puppet-user/venv/bin/python

import os

import click
import hvac
import cryptography.x509


@click.command()
@click.argument("certname")
@click.argument("input_file", type=click.File("rb"), default="-")
@click.option("--vault_addr", default=os.environ.get("VAULT_ADDR", "https://vault.home.arpa:8200"))
@click.option("--policy", default="puppet")
@click.option("--verify", default="/etc/ssl/certs/puppet_ca.pem")
def main(certname, input_file, vault_addr, policy, verify):
    """
    Check the challengePassword OID in a Certificate Signing Request (CSR) to verify if it's a valid token for logging into HashiCorp Vault.
    To be called as an autosign policy executable file:
    https://www.puppet.com/docs/puppet/8/ssl_autosign#ssl_policy_based_autosigning-custom-policy-executables

    Parameters:
    certname (str): The name of the certificate or a descriptive identifier.
    input_file (file): The input file containing the PEM-encoded CSR data. If not provided, reads from standard input ("-").
    vault_addr (str, optional): The address of the HashiCorp Vault server. Defaults to the 'VAULT_ADDR' environment variable.
    policy (str, optional): The policy name to check for in HashiCorp Vault. Defaults to "puppet".
    verify (str, optional): The location of the root CA certificate to talk SSL with vault

    Raises:
    - cryptography.x509.base.AttributeNotFound: If the 'challengePassword' attribute with the specified OID is not found in the CSR.
    - PermissionError: If the token does not have the specified policy in HashiCorp Vault.
    - hvac.exceptions.InvalidRequest: If the token passed to 'vault_client.auth.token.lookup_self()' cannot authenticate with HashiCorp Vault.

    https://www.puppet.com/docs/puppet/8/ssl_autosign#ssl_policy_based_autosigning-policy-executable-api
    The executable must exit with a status of 0 if the certificate should be autosigned; it must exit with a non-zero status if it should not be autosigned.
    The Puppet primary server treats all non-zero exit statuses as equivalent.

    Procedure:
    1. Read the CSR data from the input file.
    2. Load the CSR data into a cryptography.x509.CertificateSigningRequest object.
    3. Define the ChallengePassword OID as '1.2.840.113549.1.9.7'.
    4. Try to retrieve the ChallengePassword attribute from the CSR using the specified OID.
    5. Decode the attribute value into a token.
    6. Initialize the HashiCorp Vault client with the specified Vault address and the token from the attribute.
    7. Check if the token can authenticate itself with HashiCorp Vault.
    8. Get the list of policies associated with the token.
    9. Check if the specified policy is in the list of policies; if not, raise a PermissionError.

    Example Usage:
    ```
    $ python script.py mycertname csr.pem --vault_addr="https://vault.example.com" --policy="my_policy"
    ```
    """

    csr_data = input_file.read()
    csr = cryptography.x509.load_pem_x509_csr(csr_data)

    #challengePassword OID
    dotted_string = "1.2.840.113549.1.9.7"

    # Create an ObjectIdentifier for the ChallengePassword OID
    oid =  cryptography.x509.ObjectIdentifier(dotted_string)

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
