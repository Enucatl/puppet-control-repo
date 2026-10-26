"""Check DHCP ownership survives both migration directions and repeated apply."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "technitium_leases",
    Path(__file__).parents[1] / "provisioning/files/technitium-leases.py",
)
leases = importlib.util.module_from_spec(spec)
spec.loader.exec_module(leases)

SCOPES = [
    {
        "name": "LAN",
        "router": "192.168.1.1/24",
        "start": "192.168.1.100",
        "end": "192.168.1.200",
        "reserved_leases": ["server|02:00:00:00:00:01|192.168.1.10|Ansible"],
    }
]


def test_expired_indefinite_and_durable_holds():
    """Only new live leases import; existing holds remain until reconciliation."""
    result = leases.merge(
        SCOPES,
        "0 02:00:00:00:00:02 192.168.1.100 * *\n"
        "200 02:00:00:00:00:03 192.168.1.101 laptop *\n"
        "100 02:00:00:00:00:04 192.168.1.102 expired *\n",
        [],
        None,
        100,
    )
    assert len(result["holds"]) == 2
    assert result["holds"][0]["expires"] == 0
    assert result["holds"][0]["hostname"] == ""
    assert result["holds"][0]["sources"] == ["dnsmasq"]
    assert "02:00:00:00:00:02|192.168.1.100" in result["reserved_leases"]["LAN"]
    assert "dhcp-host=02:00:00:00:00:03,192.168.1.101\n" in result["dnsmasq"]
    assert leases.merge(SCOPES, "", result, None, 500) == result


@pytest.mark.parametrize(
    "line",
    [
        "0 02:00:00:00:00:02 192.168.1.10 x *",  # Declared IP collision.
        "0 02:00:00:00:00:01 192.168.1.100 x *",  # Declared MAC collision.
        "0 invalid 192.168.1.100 x *",
        "0 ff:ff:ff:ff:ff:ff 192.168.1.100 x *",
        "0 02:00:00:00:00:02 192.168.2.100 x *",
        "0 02:00:00:00:00:02 192.168.1.1 x *",
        "0 02:00:00:00:00:02 192.168.1.255 x *",
        "0 02:00:00:00:00:02 192.168.1.201 x *",
        "0 02:00:00:00:00:02 192.168.1.100 x|injection *",
        "-1 02:00:00:00:00:02 192.168.1.100 x *",
    ],
)
def test_invalid_ownership_fails_closed(line):
    """Reject invalid reservations and both directions of identity conflict."""
    with pytest.raises(ValueError):
        leases.merge(SCOPES, line, [], None, 100)


def test_technitium_export_and_source_merge():
    """Current API exports preserve new allocations in the rollback overlay."""
    export = {
        "status": "ok",
        "response": {
            "leases": [
                {
                    "scope": "LAN",
                    "hardwareAddress": "02-00-00-00-00-02",
                    "address": "192.168.1.100",
                    "hostName": None,
                    "leaseExpires": "2030-01-01T00:00:00Z",
                }
            ]
        },
    }
    result = leases.merge(
        SCOPES, "0 02:00:00:00:00:02 192.168.1.100 * *", [], export, 100
    )
    assert result["holds"][0]["expires"] == 0
    assert result["holds"][0]["sources"] == ["dnsmasq", "technitium"]
    assert result["dnsmasq"].count("192.168.1.100") == 1
    export["response"]["leases"][0]["address"] = "192.168.1.101"
    with pytest.raises(ValueError, match="Conflicting addresses"):
        leases.merge(SCOPES, "", result, export, 100)


def test_declared_lease_below_dynamic_pool_is_retained():
    """A declared static allocation is valid below the dynamic range."""
    result = leases.merge(
        SCOPES, "0 02:00:00:00:00:01 192.168.1.10 server *", [], None, 100
    )
    assert result["reserved_leases"]["LAN"].count("192.168.1.10") == 1
    assert len(result["holds"]) == 1
    assert result["dnsmasq"] == ""
    assert leases.merge(SCOPES, "", [], None, 100)["dnsmasq"] == ""
    assert leases.merge(SCOPES, "", result, None, 500)["dnsmasq"] == ""


def test_same_mac_in_another_scope_fails_closed():
    """A migration never guesses ownership for a MAC with multiple addresses."""
    second = {
        "name": "Guest",
        "router": "192.168.2.1/24",
        "start": "192.168.2.100",
        "end": "192.168.2.200",
        "reserved_leases": [],
    }
    with pytest.raises(ValueError, match="Conflicting addresses"):
        leases.merge(
            SCOPES + [second],
            "0 02:00:00:00:00:01 192.168.2.100 guest *",
            [],
            None,
            100,
        )


@pytest.mark.parametrize("hostname", ["server", "SERVER.home.test", "other.invalid"])
def test_import_cannot_replace_static_dns_or_escape_scope(hostname):
    """Prevent reservations from overwriting protected DNS names."""
    scopes = [{**SCOPES[0], "domain": "home.test"}]
    with pytest.raises(ValueError, match="Conflicting DNS owner|outside scope domain"):
        leases.merge(
            scopes, f"0 02:00:00:00:00:02 192.168.1.100 {hostname} *", [], None, 100
        )


def test_import_checks_static_dns_hosts_and_other_holds():
    """Non-DHCP static hosts and imported peers also own their DNS names."""
    scopes = [{**SCOPES[0], "domain": "home.test"}]
    row = "0 02:00:00:00:00:02 192.168.1.100 laptop *"
    with pytest.raises(ValueError, match="Conflicting DNS owner"):
        leases.merge(
            scopes,
            row,
            [],
            None,
            100,
            [{"name": "laptop", "addresses": ["192.168.1.99"]}],
        )
    with pytest.raises(ValueError, match="Conflicting DNS owner"):
        leases.merge(
            scopes,
            row + "\n0 02:00:00:00:00:03 192.168.1.101 laptop.home.test *",
            [],
            None,
            100,
        )
    result = leases.merge(
        scopes, row, [], None, 100, [{"name": "laptop", "addresses": ["192.168.1.100"]}]
    )
    assert len(result["holds"]) == 1


def test_historical_indefinite_lease_in_excluded_dynamic_range():
    """Preserve historical ownership below today's dynamic pool as a reservation."""
    result = leases.merge(
        SCOPES,
        "0 b0:6e:bf:82:d5:1c 192.168.1.11 computer 01:b0:6e:bf:82:d5:1c",
        [],
        None,
        100,
    )
    assert result["holds"][0]["expires"] == 0
    assert (
        "computer|b0:6e:bf:82:d5:1c|192.168.1.11|Lease continuity"
        in result["reserved_leases"]["LAN"]
    )
    assert result["dnsmasq"] == "dhcp-host=b0:6e:bf:82:d5:1c,192.168.1.11\n"
    assert leases.merge(SCOPES, "", result, None, 500) == result
