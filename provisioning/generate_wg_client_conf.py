#!/usr/bin/env python3
"""Generate WireGuard client configuration from router settings."""

import click
import yaml
from pathlib import Path


def load_router_config():
    config_path = Path(__file__).parent / "host_vars" / "router.yml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def get_client_config(router_config, client_name):
    for peer in router_config.get("wg_peers", []):
        if peer["name"] == client_name:
            return peer
    raise ValueError(f"Client '{client_name}' not found in wg_peers")


def generate_conf(
    client_name,
    client_config,
    router_config,
    server_endpoint,
    server_public_key,
    ipv6_prefix,
):
    if ipv6_prefix is None:
        ipv6_prefix = router_config.get("ipv6_prefix", "fd00")

    client_ip = client_config["ip"]
    client_ipv6 = f"{ipv6_prefix}:c8::{client_config['ipv6_host']}/128"

    conf = f"""# WireGuard configuration for {client_name}
# Generated from router configuration
# Place in /etc/wireguard/wg0.conf (or similar)
# Activate with: sudo wg-quick up wg0
#
# Your public key: {client_config["pubkey"]}
#
# IMPORTANT: Replace the PrivateKey placeholder below with your private key
# (the one that matches your public key shown above).

[Interface]
PrivateKey = REPLACE_WITH_CLIENT_PRIVATE_KEY
Address = {client_ip}
Address = {client_ipv6}
DNS = 10.0.0.1, {ipv6_prefix}::1

[Peer]
PublicKey = {server_public_key}
Endpoint = {server_endpoint}
AllowedIPs = 0.0.0.0/0, ::/0
PersistentKeepalive = 15
"""
    return conf


@click.command(
    epilog=(
        "Example:\n\n\b\n"
        "  uv run generate_wg_client_conf.py --client andrea --endpoint vpn.enucatl.com:51820 --server-pubkey $(vault kv get -field wg_server_private_key kv/puppet | wg pubkey) --ipv6-prefix $(vault kv get -field ipv6-prefix kv/puppet)"
    ),
)
@click.option(
    "--client",
    "-c",
    required=True,
    help="Client name (must match a wg_peers entry in router.yml)",
)
@click.option(
    "--endpoint",
    "-e",
    required=True,
    help="Server endpoint, e.g. vpn.enucatl.com:51820",
)
@click.option(
    "--server-pubkey",
    "-k",
    required=True,
    help="Server WireGuard public key (from: vault kv get kv/puppet)",
)
@click.option(
    "--ipv6-prefix",
    "-6",
    default=None,
    help="IPv6 prefix (overrides value from router.yml)",
)
@click.option(
    "--output",
    "-o",
    default=None,
    help="Output file (default: ~/Downloads/<client>.conf)",
)
def main(client, endpoint, server_pubkey, ipv6_prefix, output):
    if output:
        output_file = output
    else:
        output_file = Path.home() / "Downloads" / f"{client}.conf"

    router_config = load_router_config()
    client_config = get_client_config(router_config, client)

    conf = generate_conf(
        client, client_config, router_config, endpoint, server_pubkey, ipv6_prefix
    )

    with open(output_file, "w") as f:
        f.write(conf)

    click.echo(f"Generated {output_file}")
    click.echo(f"  Client IP: {client_config['ip']}")
    click.echo(f"  Client public key: {client_config['pubkey']}")
    click.echo(f"  Server endpoint: {endpoint}")
    click.echo(
        f"\nNOTE: Edit {output_file} and replace REPLACE_WITH_CLIENT_PRIVATE_KEY with the"
    )
    click.echo(f"private key corresponding to the public key shown above.")


if __name__ == "__main__":
    main()
