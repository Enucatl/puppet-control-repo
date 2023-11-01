#!/opt/puppet-user/venv/bin/python

import os

import click
import yaml


@click.command()
@click.argument("certname")
@click.argument("output_file", type=click.File("w"), default="-")
@click.option("--domain", default="home.arpa")
def main(certname, output_file, domain):
    """External Node Classifier (ENC) for Puppet.

    Args:
        certname (str): The certificate name of the client node.
        output_file (str, optional): The file to write the YAML node
            configuration to. Defaults to standard output.
        domain (str, optional): The default domain to use for matching
            environments. Defaults to "home.arpa".

    Example Usage:
        $ python enc.py <certname> --domain <domain> --output-file
            <output_file>

    If the node has a name that ends with `dev.domain`,
    then its environment is set to dev, otherwise to production.

    For Puppet to use this ENC, specify it in your Puppet master's
    configuration file.

    Note: This script is a basic example. You can extend it to support more
    complex node classifications based on your infrastructure requirements.

    Returns:
        None
    """

    output = {
        "parameters": {
        },
    }
    if certname.endswith(f".dev.{domain}"):
        output["environment"] = "dev"
    else:
        output["environment"] = "production"
    yaml.dump(output, output_file)
    
    
if __name__ == "__main__":
    main()
