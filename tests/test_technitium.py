"""Exercise rendered configuration against an isolated Technitium server."""

import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import threading
import sys

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).parents[1] / "provisioning"
sys.path.insert(0, str(ROOT / "src"))

from router_technitium import bootstrap, configure
from router_technitium.api import Api


@pytest.fixture(scope="module")
def rendered(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Render with Ansible's real filters and recursively resolved host variables."""
    if not shutil.which("ansible-playbook"):
        pytest.skip("Use the provisioning environment for Ansible rendering")
    directory = tmp_path_factory.mktemp("technitium")
    tasks = [
        {
            "ansible.builtin.template": {
                "src": str(ROOT / "templates/app_configs/technitium.json.j2"),
                "dest": str(directory / "config.json"),
            }
        },
        {
            "ansible.builtin.copy": {
                "content": "{{ {'printer': printer_ip, 'wireguard': wg_subnet, 'recursion_acl': technitium_recursion_acl} | to_json }}",
                "dest": str(directory / "declarations.json"),
            }
        },
    ]
    play = [
        {
            "hosts": "localhost",
            "connection": "local",
            "gather_facts": False,
            "vars_files": [
                str(ROOT / "host_vars/router.yml"),
                str(ROOT / "vars/technitium.yml"),
            ],
            "vars": {"ipv6_prefix": "2001:db8:1234"},
            "tasks": tasks,
        }
    ]
    play_path = directory / "render.yml"
    play_path.write_text(yaml.safe_dump(play))
    result = subprocess.run(
        ["ansible-playbook", "-i", "localhost,", str(play_path)],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    output = json.loads((directory / "config.json").read_text())
    output.update(json.loads((directory / "declarations.json").read_text()))
    return output


def expected_dns(rendered: dict) -> dict:
    """List the expected exact and wildcard addresses from the configuration."""
    expected = {}
    for host in rendered["hosts"]:
        name = host["name"] + "." + rendered["domain"]
        expected[name] = {str(ipaddress.ip_address(a)) for a in host["addresses"]}
        if host.get("wildcard"):
            expected["*." + name] = expected[name]
    return expected


def test_configuration_and_persistence(rendered: dict, tmp_path: Path) -> None:
    """Bootstrap, preserve DHCP on repeat applies, and verify real DNS policies."""
    base = os.environ.get("TECHNITIUM_API_URL")
    if not base:
        pytest.skip("Set TECHNITIUM_API_URL for an isolated Technitium 15.6.2 server")
    import urllib.parse
    import urllib.request

    def api(path: str, **parameters: object) -> dict:
        """Call the test server and require application-level success."""
        request = urllib.request.Request(
            base + path, data=urllib.parse.urlencode(parameters).encode()
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
        assert result["status"] == "ok", result
        return result

    token = api("user/login", user="admin", **{"pass": "test-password"})["token"]
    if os.environ.get("TECHNITIUM_VERIFY_PERSISTED") != "1":
        config = dict(rendered)
        config["settings"] = dict(
            rendered["settings"], dnsServerLocalEndPoints="127.0.0.1:5354,[::1]:5354"
        )
        with Api("test-password", base_url=base) as client:
            bootstrap(client, config)
            # A reservation added after initial setup must survive ordinary applies.
            scope = config["scopes"][0]
            reservations = scope["reserved_leases"] + [
                f"existing|02:00:00:aa:bb:ee|{scope['end']}|Existing lease"
            ]
            client(
                "dhcp/scopes/set",
                name=scope["name"],
                reservedLeases="|".join(reservations),
            )
            before = {
                scope["name"]: client("dhcp/scopes/get", name=scope["name"])["response"]
                for scope in config["scopes"]
            }
            for _ in range(2):
                configure(client, config)
                assert {
                    scope["name"]: client("dhcp/scopes/get", name=scope["name"])[
                        "response"
                    ]
                    for scope in config["scopes"]
                } == before
    with Api("test-password", base_url=base) as client:
        with pytest.raises(RuntimeError, match="Bootstrap"):
            bootstrap(client, rendered)
    with pytest.raises(RuntimeError) as failure:
        with Api("incorrect-test-password", base_url=base):
            pytest.fail("Invalid credentials must not open a session")
    assert "incorrect-test-password" not in str(failure.value)
    zones = api("zones/list", token=token)["response"]["zones"]
    names = {zone["name"] for zone in zones}
    expected_reverse = {
        "8.3.7.1.2.2.7.d.b.6.d.f.ip6.arpa",
        "4.3.2.1.8.b.d.0.1.0.0.2.ip6.arpa",
    }
    assert {name for name in names if name.endswith("ip6.arpa")} == expected_reverse
    for zone in {
        rendered["domain"],
        rendered["intranet"],
        "10.in-addr.arpa",
    } | expected_reverse:
        options = api("zones/options/get", token=token, zone=zone)["response"]
        assert options["queryAccess"] == "UseSpecifiedNetworkACL"
        assert options["zoneTransfer"] == "Deny"
        assert {
            ipaddress.ip_network(value) for value in options["queryAccessNetworkACL"]
        } == {ipaddress.ip_network(value) for value in rendered["local_query_acl"]}
    for scope in rendered["scopes"]:
        observed = api("dhcp/scopes/get", token=token, name=scope["name"])["response"]
        listed = api("dhcp/scopes/list", token=token)["response"]["scopes"]
        assert all(not item["enabled"] for item in listed)
        assert observed["startingAddress"] == str(
            ipaddress.ip_interface(scope["router"]).ip
        )
        assert observed["endingAddress"] == scope["end"]
        assert (
            observed["leaseTimeDays"] * 24 + observed["leaseTimeHours"]
            == scope["lease_hours"]
        )
        assert observed["dnsServers"] == [rendered["dns"]]
        assert observed["dnsUpdates"] == ("domain" in scope)
        assert observed["dnsOverwriteForDynamicLease"] is False
        assert len(observed["reservedLeases"]) == len(scope["reserved_leases"]) + (
            scope == rendered["scopes"][0]
        )
        assert observed["exclusions"] == [
            {
                "startingAddress": str(ipaddress.ip_interface(scope["router"]).ip),
                "endingAddress": str(ipaddress.ip_address(scope["start"]) - 1),
            }
        ]
        assert observed.get("bootFileName", "") == scope.get("boot_file", "")
        if "boot_server" in scope:
            assert observed["serverAddress"] == scope["boot_server"]
    records = api(
        "zones/records/get", token=token, domain=rendered["domain"], listZone="true"
    )["response"]["records"]
    actual = {}
    for record in records:
        if record["type"] in ["A", "AAAA"]:
            actual.setdefault(record["name"], set()).add(
                str(ipaddress.ip_address(record["rData"]["ipAddress"]))
            )
    for owner, addresses in expected_dns(rendered).items():
        assert actual[owner] == addresses, owner
    intranet_records = api(
        "zones/records/get", token=token, domain=rendered["intranet"], listZone="true"
    )["response"]["records"]
    alias = next(record for record in intranet_records if record["type"] == "DNAME")
    assert alias["name"] == rendered["intranet"]
    assert alias["rData"]["dname"] == rendered["domain"]
    for host in rendered["hosts"]:
        if host.get("ptr"):
            for address in host["addresses"]:
                owner = ipaddress.ip_address(address).reverse_pointer
                records = api("zones/records/get", token=token, domain=owner)[
                    "response"
                ]["records"]
                assert [
                    r["rData"]["ptrName"] for r in records if r["type"] == "PTR"
                ] == [host["name"] + "." + rendered["domain"]]

    if os.environ.get("TECHNITIUM_SOURCE_TEST") != "1":
        return
    # Source addresses live only on this disposable container's loopback interface.
    sources = [(rendered["printer"], True)]
    ula = next(host for host in rendered["hosts"] if host["name"] == "router")[
        "addresses"
    ][1]
    ula_prefix = ":".join(ipaddress.ip_address(ula).exploded.split(":")[:3])
    for scope in rendered["scopes"]:
        network = ipaddress.ip_interface(scope["router"]).network
        subnet = int(str(network.network_address).split(".")[2])
        allowed = scope["name"] not in ["IoT", "Guest"]
        sources.extend(
            [
                (str(network.network_address + 220), allowed),
                (f"{ula_prefix}:{subnet:x}::220", allowed),
                (f"2001:db8:1234:{subnet:x}::220", allowed),
            ]
        )
    sources.extend(
        [
            (
                str(
                    ipaddress.ip_network(
                        rendered["wireguard"], strict=False
                    ).network_address
                    + 220
                ),
                True,
            ),
            (f"{ula_prefix}:c8::220", True),
            ("2001:db8:1234:c8::220", True),
        ]
    )
    settings = api("settings/get", token=token)["response"]
    assert settings["recursion"] == "UseSpecifiedNetworkACL"
    assert {
        ipaddress.ip_network(value) for value in settings["recursionNetworkACL"]
    } == {ipaddress.ip_network(value) for value in rendered["recursion_acl"]}
    upstream = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    upstream.bind(("127.0.0.1", 0))
    forwarded = []

    def answer_public() -> None:
        """Answer recursive A queries locally without depending on public DNS."""
        while True:
            try:
                request, peer = upstream.recvfrom(4096)
            except OSError:
                return
            end = 12
            while request[end]:
                end += request[end] + 1
            end += 5
            forwarded.append(request)
            reply = (
                request[:2] + struct.pack("!5H", 0x8180, 1, 1, 0, 0) + request[12:end]
            )
            reply += (
                b"\xc0\x0c"
                + struct.pack("!HHIH", 1, 1, 300, 4)
                + socket.inet_aton("192.0.2.10")
            )
            try:
                upstream.sendto(reply, peer)
            except OSError:
                return

    threading.Thread(target=answer_public, daemon=True).start()
    api(
        "settings/set",
        token=token,
        forwarders=f"127.0.0.1:{upstream.getsockname()[1]}",
        forwarderProtocol="Udp",
        dnssecValidation="false",
    )
    api("cache/flush", token=token)
    api(
        "zones/records/add",
        token=token,
        zone=rendered["domain"],
        domain="new-dhcp-client." + rendered["domain"],
        type="A",
        ipAddress="10.0.0.123",
        overwrite="true",
    )
    atlas = next(host for host in rendered["hosts"] if host["name"] == "atlas")
    remarkable = next(
        host for host in rendered["hosts"] if host["name"] == "remarkable"
    )
    protected = [
        ("atlas." + rendered["domain"], 1),
        ("atlas." + rendered["intranet"], 1),
        ("service.atlas." + rendered["intranet"], 1),
        ("new-dhcp-client." + rendered["intranet"], 1),
        ("_apt_proxy._tcp." + rendered["intranet"], 33),
    ] + [
        (ipaddress.ip_address(address).reverse_pointer, 12)
        for address in [
            atlas["addresses"][0],
            remarkable["addresses"][1],
            remarkable["addresses"][2],
        ]
    ]
    for source, allowed in sources:
        address = ipaddress.ip_address(source)
        subprocess.run(
            [
                "ip",
                "address",
                "replace",
                f"{source}/{address.max_prefixlen}",
                "dev",
                "lo",
            ]
            + (["nodad"] if address.version == 6 else []),
            check=True,
        )
        for owner, query_type in protected + [("public.example", 1)]:
            labels = (
                b"".join(
                    bytes([len(label)]) + label.encode() for label in owner.split(".")
                )
                + b"\0"
            )
            request = (
                struct.pack("!6H", 1234, 0x100, 1, 0, 0, 0)
                + labels
                + struct.pack("!HH", query_type, 1)
            )
            family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
            target = "::1" if address.version == 6 else "127.0.0.1"
            with socket.socket(family, socket.SOCK_DGRAM) as client:
                client.settimeout(1)
                client.bind((source, 0))
                # Adding addresses can briefly restart Technitium's listeners.
                for attempt in range(3):
                    client.sendto(request, (target, 5354))
                    try:
                        reply = client.recv(4096)
                        break
                    except TimeoutError:
                        if attempt == 2:
                            pytest.fail(f"DNS query timed out: {source} -> {owner}")
            header = struct.unpack("!6H", reply[:12])
            expected_rcode = 0 if allowed or owner == "public.example" else 5
            assert header[1] & 15 == expected_rcode, (source, owner, header)
            assert bool(header[3]) == (expected_rcode == 0), (source, owner, header)
            if owner == "public.example":
                assert socket.inet_aton("192.0.2.10") in reply
            elif owner.endswith(rendered["intranet"]) and allowed:
                if owner.startswith("new-dhcp-client."):
                    assert socket.inet_aton("10.0.0.123") in reply
                elif query_type == 1:
                    assert socket.inet_aton(atlas["addresses"][0]) in reply
                else:
                    assert b"apt-cacher-ng" in reply

    assert forwarded, "Public answer must come from the synthetic upstream"
    upstream.close()


@pytest.mark.parametrize("loads", [True, False])
def test_enable_dhcp_waits_for_saved_scopes(
    monkeypatch: pytest.MonkeyPatch, loads: bool
) -> None:
    """Never enable a partial set of scopes while saved configuration is loading."""
    from router_technitium import enable_dhcp

    monkeypatch.setattr("router_technitium.time.sleep", lambda _: None)
    scopes = [{"name": "LAN"}, {"name": "IoT"}]
    listed = 0
    enabled = []

    def api(path: str, **parameters: object) -> dict:
        """Expose all saved scopes only after two incomplete responses."""
        nonlocal listed
        if path == "dhcp/scopes/list":
            listed += 1
            return {
                "response": {"scopes": scopes if loads and listed >= 3 else scopes[:1]}
            }
        assert path == "dhcp/scopes/enable"
        assert listed >= 3
        enabled.append(parameters["name"])
        return {}

    if loads:
        enable_dhcp(api, {"scopes": scopes})
        assert enabled == ["LAN", "IoT"]
    else:
        with pytest.raises(RuntimeError, match="did not finish loading"):
            enable_dhcp(api, {"scopes": scopes})
        assert enabled == []
