"""Check Atlas I2P forwarding and retirement of the old Tor relay rules."""

from pathlib import Path
import re
import shlex


PROVISIONING = Path(__file__).resolve().parents[1] / "provisioning"


def test_i2p_tag_selects_only_the_migration_rules() -> None:
    """Keep the targeted apply from including other rendered router settings."""
    task = (PROVISIONING / "tasks/08_i2p.yml").read_text()
    pattern = re.search(r"i2p_rule_pattern: '([^']+)'", task)
    assert pattern is not None
    lines = "\n".join(
        (PROVISIONING / "templates/partials" / name).read_text()
        for name in ("nat_routing.j2", "firewall.j2")
    ).splitlines()
    prefixes = {"set nat destination rule 22"} | {
        f"set firewall {family} name {chain} rule {rule}"
        for family, chain in (("ipv4", "WAN-FORWARD"), ("ipv6", "WAN-FORWARD6"))
        for rule in (41, 42)
    }
    selected = {line for line in lines if re.match(pattern[1], line)}
    assert selected == {
        line for line in lines if any(line.startswith(p + " ") for p in prefixes)
    }
    assert all(any(line.startswith(p + " ") for line in selected) for p in prefixes)
    assert "select('match', i2p_rule_pattern)" in task
    assert "backup: true" in task and "save: true" in task
    assert "tags: [i2p, never]" in (PROVISIONING / "router.yml").read_text()


def test_i2p_forwarding_retires_tor_and_limits_public_ports() -> None:
    """Apply the same I2P destination and abuse limits for both IP families."""
    variables = dict(
        re.findall(
            r'^(\w+): "([^"\n]*)"$',
            (PROVISIONING / "host_vars/router.yml").read_text(),
            re.MULTILINE,
        )
    )
    text = "\n".join(
        (PROVISIONING / "templates/partials" / name).read_text()
        for name in ("nat_routing.j2", "firewall.j2")
    )
    for name, value in variables.items():
        text = text.replace("{{ " + name + " }}", value)
    commands = {
        " ".join(parts[:-1]): parts[-1]
        for line in text.splitlines()
        if line.startswith("set ")
        for parts in [shlex.split(line)]
    }
    nat = "set nat destination rule 22"
    assert commands[nat + " translation address"] == variables["atlas_node_ip"]
    assert commands[nat + " destination port"] == "11001"
    assert commands[nat + " protocol"] == "tcp_udp"
    deletes = (PROVISIONING / "templates/partials/deletes.j2").read_text().splitlines()
    assert not any(
        line.startswith("set nat destination rule 20 ") for line in text.splitlines()
    )
    assert "delete nat destination rule 20" in deletes
    for family, chain, field in (
        ("ipv4", "WAN-FORWARD", "ip"),
        ("ipv6", "WAN-FORWARD6", "ipv6_suffix"),
    ):
        prefix = f"set firewall {family} name {chain} rule"
        for rule in (39, 40):
            assert not any(key.startswith(f"{prefix} {rule} ") for key in commands)
            assert f"delete firewall {family} name {chain} rule {rule}" in deletes
        for rule in (41, 42):
            assert (
                commands[f"{prefix} {rule} destination address"]
                == variables[f"atlas_node_{field}"]
            )
            assert commands[f"{prefix} {rule} destination port"] == "11001"
            assert commands[f"{prefix} {rule} protocol"] == "tcp_udp"
            if family == "ipv6":
                assert commands[f"{prefix} {rule} destination address-mask"] == (
                    "::ffff:ffff:ffff:ffff"
                )
        assert commands[f"{prefix} 41 action"] == "drop"
        assert commands[f"{prefix} 41 recent count"] == "50"
        assert commands[f"{prefix} 41 recent time"] == "minute"
        assert commands[f"{prefix} 41 state"] == "new"
        assert commands[f"{prefix} 42 action"] == "accept"
