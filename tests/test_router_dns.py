"""Exercise the real router DNS smoke helpers without network requests."""

import ipaddress
from pathlib import Path
import re
import shlex
import subprocess


PROVISIONING = Path(__file__).resolve().parents[1] / "provisioning"


def test_infrastructure_dns_checks_preserve_names_and_canonical_addresses() -> None:
    """Query each endpoint once per transport/type despite shell helper globals."""
    dnsmasq = (PROVISIONING / "templates/app_configs/dnsmasq.conf.j2").read_text()
    assert (
        "host-record=atlas.{{ dhcp_domain }},vault.{{ dhcp_domain }},"
        "openvox.{{ dhcp_domain }},ipa.{{ dhcp_domain }},"
        "{{ atlas_node_ip }},{{ atlas_node_ula }}"
    ) in dnsmasq
    assert "address=/freeipa.{{ dhcp_domain }}/{{ docker_node_ip }}" in dnsmasq
    assert "address=/docker.{{ dhcp_domain }}/{{ docker_node_ip }}" in dnsmasq
    assert "address=/atlas.{{ dhcp_domain }}/{{ atlas_node_ip }}" in dnsmasq
    assert "address=/atlas.{{ dhcp_domain }}/{{ atlas_node_ula }}" in dnsmasq
    variables = dict(
        re.findall(
            r'^(\w+): "([^"\n]*)"$',
            (PROVISIONING / "host_vars/router.yml").read_text(),
            re.MULTILINE,
        )
    )
    declared_ipv6 = variables["atlas_node_ula"].replace(
        "{{ ula_prefix }}", variables["ula_prefix"]
    )
    canonical_ipv6 = ipaddress.IPv6Address(declared_ipv6).compressed
    assert declared_ipv6 == canonical_ipv6
    template = (
        PROVISIONING / "templates/app_configs/router-smoke-check.sh.j2"
    ).read_text()
    helpers = template.split('log "START router smoke checks"', 1)[0]
    loop = re.search(
        r"for \w+ in atlas vault openvox ipa traefik.atlas authelia.atlas; do\n.*?\ndone",
        template,
        re.DOTALL,
    )
    assert loop is not None
    checks = loop.group().replace("{{ atlas_node_ip }}", variables["atlas_node_ip"])
    checks = checks.replace("{{ atlas_node_ula }}", declared_ipv6)
    recorder = r"""
ADGUARD_V4=10.0.0.1
ADGUARD_V6=fd6b:d722:1738::1
DNSMASQ_PORT=5300
DHCP_DOMAIN=home.arpa
retry_line() {
    printf '%s|%s|' "$1" "$2"
    shift 3
    printf '%s\n' "$*"
}
"""
    result = subprocess.run(
        ["sh"],
        input=helpers + recorder + checks,
        text=True,
        capture_output=True,
        check=True,
        timeout=5,
    )
    queries = result.stdout.splitlines()
    assert len(queries) == 36
    actual = set()
    for query in queries:
        _description, expected, command = query.split("|", 2)
        arguments = shlex.split(command)
        server = next(argument for argument in arguments if argument.startswith("@"))
        actual.add((arguments[-3], arguments[-2], server, expected))
        if server == "@127.0.0.1":
            assert arguments[arguments.index("-p") + 1] == "5300"
    assert actual == {
        (f"{name}.home.arpa", record_type, server, address)
        for name in (
            "atlas",
            "vault",
            "openvox",
            "ipa",
            "traefik.atlas",
            "authelia.atlas",
        )
        for record_type, address in (
            ("A", variables["atlas_node_ip"]),
            ("AAAA", canonical_ipv6),
        )
        for server in ("@10.0.0.1", "@fd6b:d722:1738::1", "@127.0.0.1")
    }
