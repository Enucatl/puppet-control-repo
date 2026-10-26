"""Exercise reservation and DNS continuity against the actual isolated server."""

import importlib.util
import json
import os
from pathlib import Path
import urllib.parse
import urllib.request

import pytest

spec = importlib.util.spec_from_file_location(
    "technitium_leases",
    Path(__file__).parents[1] / "provisioning/files/technitium-leases.py",
)
leases = importlib.util.module_from_spec(spec)
spec.loader.exec_module(leases)


def test_reservation_import_and_persistence():
    """Import holds without changing scope options, and seed DNS before renewal."""
    base = os.environ.get("TECHNITIUM_API_URL")
    if not base:
        pytest.skip("Set TECHNITIUM_API_URL for an isolated Technitium server")

    def api(path, **parameters):
        """Require a successful response from the disposable server."""
        request = urllib.request.Request(
            base + path, data=urllib.parse.urlencode(parameters).encode()
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
        assert result["status"] == "ok", result
        return result

    token = api("user/login", user="admin", **{"pass": "test-password"})["token"]
    name = "MigrationContinuity"
    scopes = [
        {
            "name": name,
            "router": "198.18.1.1/24",
            "start": "198.18.1.100",
            "end": "198.18.1.200",
            "domain": "migration.test",
            "reserved_leases": [],
        }
    ]
    merged = leases.merge(
        scopes,
        "0 02:00:00:00:01:01 198.18.1.11 before-renew *\n"
        "0 02:00:00:00:01:02 198.18.1.101 * *",
        [],
        None,
        100,
    )
    if os.environ.get("TECHNITIUM_VERIFY_PERSISTED") != "1":
        api(
            "dhcp/scopes/set",
            token=token,
            name=name,
            startingAddress="198.18.1.1",
            endingAddress="198.18.1.200",
            subnetMask="255.255.255.0",
            exclusions="198.18.1.1|198.18.1.99",
            leaseTimeHours=7,
            domainName="migration.test",
            dnsUpdates="true",
            dnsOverwriteForDynamicLease="false",
            routerAddress="198.18.1.1",
            useThisDnsServer="false",
            dnsServers="198.18.1.1",
            serverAddress="198.18.1.2",
            bootFileName="migration-boot",
            dnsTtl=300,
        )
        api("dhcp/scopes/disable", token=token, name=name)
        before = api("dhcp/scopes/get", token=token, name=name)["response"]
        for _ in range(2):
            api(
                "dhcp/scopes/set",
                token=token,
                name=name,
                reservedLeases=merged["reserved_leases"][name],
            )
        after = api("dhcp/scopes/get", token=token, name=name)["response"]
        assert {
            key: value for key, value in after.items() if key != "reservedLeases"
        } == {key: value for key, value in before.items() if key != "reservedLeases"}
    scope = api("dhcp/scopes/get", token=token, name=name)["response"]
    assert {row["address"] for row in scope["reservedLeases"]} == {
        "198.18.1.11",
        "198.18.1.101",
    }
    snapshot_root = os.environ.get("TECHNITIUM_ROLLBACK_CONFIG")
    if snapshot_root:
        directory = Path(snapshot_root) / "state"
        directory.mkdir(exist_ok=True)
        path = directory / "migration-reservations.json"
        observed = sorted(
            scope["reservedLeases"], key=lambda row: row["hardwareAddress"]
        )
        print("Observed reservation state:", json.dumps(observed, sort_keys=True))
        if os.environ.get("TECHNITIUM_VERIFY_PERSISTED") == "1":
            expected = json.loads(path.read_text())
            print(
                "Pre-restart reservation state:", json.dumps(expected, sort_keys=True)
            )
            # The server serializes an unknown hostname as null after restart.
            assert [dict(row, hostName=row["hostName"] or "") for row in observed] == [
                dict(row, hostName=row["hostName"] or "") for row in expected
            ]
        else:
            path.write_text(json.dumps(observed))
    listed = api("dhcp/scopes/list", token=token)["response"]["scopes"]
    assert not next(row for row in listed if row["name"] == name)["enabled"]
    assert scope["leaseTimeHours"] == 7
    assert scope["bootFileName"] == "migration-boot"
    records = api(
        "zones/records/get", token=token, domain="migration.test", listZone="true"
    )["response"]["records"]
    assert [
        (row["name"], row["rData"]["ipAddress"])
        for row in records
        if row["type"] == "A"
    ] == [("before-renew.migration.test", "198.18.1.11")]
    reverse = api(
        "zones/records/get",
        token=token,
        domain="1.18.198.in-addr.arpa",
        listZone="true",
    )["response"]["records"]
    assert [
        (row["name"], row["rData"]["ptrName"])
        for row in reverse
        if row["type"] == "PTR"
    ] == [("11.1.18.198.in-addr.arpa", "before-renew.migration.test")]
    # Real API response shape is accepted even with no allocated DHCP clients.
    rollback = leases.merge(
        scopes, "", merged, api("dhcp/leases/list", token=token), 500
    )
    assert rollback["holds"] == merged["holds"]
    assert rollback["dnsmasq"] == merged["dnsmasq"]
    original_fields = merged["reserved_leases"][name].split("|")
    restored_fields = rollback["reserved_leases"][name].split("|")
    assert sorted(
        original_fields[i : i + 4] for i in range(0, len(original_fields), 4)
    ) == sorted(restored_fields[i : i + 4] for i in range(0, len(restored_fields), 4))
