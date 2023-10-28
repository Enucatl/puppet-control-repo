#!/opt/puppet-user/venv/bin/python

import os

import click
import yaml


@click.command()
@click.argument("certname")
@click.argument("output_file", type=click.File("w"), default="-")
@click.option("--domain", default="home.arpa")
def main(certname, output_file, domain):
    """
    External Node Classifier (ENC) for Puppet
    
    This Python script acts as an External Node Classifier (ENC) for Puppet, allowing you
    to define Puppet node configurations based on the certname of a client node.

    Parameters:
    certname (str): The certificate name of the client node.
    output_file (str, optional): The file to write the YAML node configuration to. Defaults to standard output.
    domain (str, optional): The default domain to use for matching environments. Defaults to "home.arpa".

    Example Usage:
    $ python enc.py <certname> <output_file> --domain <domain>

    The script determines the environment for the Puppet node based on the certname
    and outputs a YAML configuration file with the specified environment and any
    additional parameters you may want to define.

    For Puppet to use this ENC, specify it in your Puppet master's configuration file.

    Note: This script is a basic example. You can extend it to support more complex
    node classifications based on your infrastructure requirements.

    Args:
    - certname (str): The certname of the Puppet client node.
    - output_file (str): The path to the output file where the YAML node configuration will be saved.
    - domain (str, optional): The default domain for environment matching.

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
