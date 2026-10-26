"""Render staged DNS and offline DHCP candidates and check the real DNS API."""

import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import struct
import subprocess
import tempfile
import threading

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).parents[1] / "provisioning"


@pytest.fixture(scope="module")
def rendered(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Render with Ansible's real filters and recursively resolved host variables."""
    if not shutil.which("ansible-playbook"):
        pytest.skip("Use the provisioning environment for Ansible rendering")
    directory = tmp_path_factory.mktemp("technitium")
    tasks = []
    for full, name in [(False, "staged"), (True, "candidate")]:
        tasks.append(
            {
                "ansible.builtin.template": {
                    "src": str(
                        ROOT / "templates/app_configs/technitium-configure.sh.j2"
                    ),
                    "dest": str(directory / name),
                },
                "vars": {"technitium_prepare_full": full},
            }
        )
    tasks.extend(
        [
            {
                "ansible.builtin.template": {
                    "src": str(ROOT / "legacy/templates/dnsmasq.conf.j2"),
                    "dest": str(directory / "dnsmasq"),
                }
            },
            {
                "ansible.builtin.copy": {
                    "content": "{{ {'hosts': technitium_dns_hosts, 'scopes': technitium_dhcp_scopes, 'domain': dhcp_domain, 'intranet': technitium_intranet_domain, 'dns': (vyos_lan_ip | ansible.utils.ipaddr('address')), 'acl': technitium_local_query_acl, 'printer': printer_ip, 'wireguard': wg_subnet, 'recursion_acl': technitium_recursion_acl} | to_json }}",
                    "dest": str(directory / "declarations.json"),
                }
            },
        ]
    )
    play = [
        {
            "hosts": "localhost",
            "connection": "local",
            "gather_facts": False,
            "vars_files": [
                str(ROOT / "host_vars/router.yml"),
                str(ROOT / "vars/technitium.yml"),
                str(ROOT / "vars/technitium-legacy.yml"),
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
    output = {
        name: (directory / name).read_text()
        for name in ["staged", "candidate", "dnsmasq"]
    }
    output.update(json.loads((directory / "declarations.json").read_text()))
    return output


def test_modes_and_safety(rendered: dict) -> None:
    """Keep normal apply DNS-only and the complete candidate isolated and disabled."""
    for name in ["staged", "candidate"]:
        subprocess.run(["bash", "-n"], input=rendered[name], text=True, check=True)
    assert "api dhcp/scopes/set" not in rendered["staged"]
    assert "production-configs.tgz" not in rendered["staged"]
    candidate = rendered["candidate"]
    for script in (rendered["staged"], candidate):
        for line in script.splitlines():
            if line.lstrip().startswith("token=$(api user/login"):
                assert line.rstrip().endswith("| jq -er .token)"), line
    assert candidate.index("production-configs.tgz") < candidate.index("podman run")
    assert (
        'cp /config/containers/dnsmasq/leases/dnsmasq.leases "$backup/dnsmasq.leases"'
        in candidate
    )
    assert 'sha256sum --check "$backup/production.sha256"' in candidate
    assert 'cmp - "$backup/containers.txt"' in candidate
    assert (
        candidate.index("expected_state=$(candidate_state | sha256sum)")
        < candidate.index('podman stop --time 30 "$container"')
        < candidate.index(
            'podman start "$container"', candidate.index("expected_state=")
        )
    )
    assert '[[ $(candidate_state | sha256sum) == "$expected_state" ]]' in candidate
    assert "-candidate" in candidate and "container=technitium-candidate" in candidate
    assert "--network none" in candidate
    guard = candidate.index("[[ $(podman inspect")
    assert guard < candidate.index("api dhcp/scopes/set")
    assert "== none ]]" in candidate
    assert candidate.count("api dhcp/scopes/set") == 5
    assert candidate.count("api dhcp/scopes/disable") == 5
    assert "api dhcp/scopes/enable" not in candidate
    assert "all(.response.scopes[]; .enabled == false)" in candidate
    play = yaml.safe_load((ROOT / "router.yml").read_text())[0]
    preparation = next(
        task
        for task in play["tasks"]
        if task.get("vars", {}).get("technitium_prepare_full")
    )
    assert set(preparation["tags"]) == {"technitium-prepare", "never"}
    tasks = yaml.safe_load((ROOT / "tasks/12_technitium.yml").read_text())
    commit = next(task for task in tasks if "vyos.vyos.vyos_config" in task)
    assert commit["when"] == "not (technitium_prepare_full | default(false))"
    # List tasks without loading encrypted production variables or running anything.
    play["vars_files"] = []
    play["vars"] = {}
    play["pre_tasks"] = []
    for task in play["tasks"]:
        if "ansible.builtin.import_tasks" in task:
            task["ansible.builtin.import_tasks"] = str(
                ROOT / task["ansible.builtin.import_tasks"]
            )
    with tempfile.TemporaryDirectory() as directory:
        (Path(directory) / "tasks").symlink_to(ROOT / "tasks", target_is_directory=True)
        path = Path(directory) / "list.yml"
        path.write_text(yaml.safe_dump([play]))
        listed = subprocess.run(
            [
                "ansible-playbook",
                "-i",
                "router,",
                str(path),
                "--list-tasks",
                "--tags",
                "apply",
            ],
            text=True,
            capture_output=True,
        )
    assert listed.returncode == 0, listed.stdout + listed.stderr
    assert (
        listed.stdout.count(
            "Configure Technitium with isolated networking on first startup"
        )
        == 1
    )
    assert "technitium-prepare" not in listed.stdout


def expected_dns(rendered: dict) -> dict:
    """Read existing dnsmasq records independently of the candidate declarations."""
    expected = {}
    for line in rendered["dnsmasq"].splitlines():
        if line.startswith("address=/"):
            _, name, address = line.split("/")
            for owner in [name, "*." + name]:
                expected.setdefault(owner, set()).add(address)
        elif line.startswith("host-record="):
            fields = line.removeprefix("host-record=").split(",")
            names, addresses = [], []
            for field in fields:
                try:
                    ipaddress.ip_address(field)
                    addresses.append(field)
                except ValueError:
                    names.append(field)
            for name in names:
                expected.setdefault(name, set()).update(addresses)
    return {
        owner: {str(ipaddress.ip_address(address)) for address in addresses}
        for owner, addresses in expected.items()
    }


def test_static_and_reservation_parity(rendered: dict) -> None:
    """Retain all current static names, wildcard addresses and eight reservations."""
    actual = {}
    for host in rendered["hosts"]:
        name = host["name"] + "." + rendered["domain"]
        actual[name] = {
            str(ipaddress.ip_address(address)) for address in host["addresses"]
        }
        if host.get("wildcard"):
            actual["*." + name] = actual[name]
    for owner, addresses in expected_dns(rendered).items():
        assert actual[owner] == addresses, owner
    original = set()
    for line in rendered["dnsmasq"].splitlines():
        if line.startswith("dhcp-host="):
            fields = line.removeprefix("dhcp-host=").split(",")
            address = next(
                field for field in fields if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", field)
            )
            original.add((fields[0].lower(), address))
    reservations = [
        entry.split("|")
        for scope in rendered["scopes"]
        for entry in scope["reserved_leases"]
    ]
    assert {(entry[1].lower(), entry[2]) for entry in reservations} == original
    assert len(reservations) == 8


def test_candidate_api_twice(rendered: dict, tmp_path: Path) -> None:
    """Execute the complete candidate script twice against isolated Technitium."""
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
    config = tmp_path / "config"
    for name, content in {
        "config.boot": "synthetic config",
        "containers/adguard/conf/AdGuardHome.yaml": "synthetic config",
        "containers/dnsmasq/conf/dnsmasq.conf": rendered["dnsmasq"],
        "containers/dnsmasq/leases/dnsmasq.leases": "",
        "containers/alloy/conf/config.alloy": "synthetic config",
        "containers/technitium-candidate/admin-password": "test-password",
    }.items():
        path = config / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    commands = tmp_path / "commands"
    commands.mkdir()
    podman = commands / "podman"
    podman.write_text("""#!/bin/bash
set -euo pipefail
case "$1" in
exec) shift 2; exec "$@";;
inspect)
    case "$*" in
    *HostConfig.NetworkMode*) echo none;;
    *) echo 'synthetic container identity';;
    esac;;
run|stop|start|rm) exit 0;;
*) exit 2;;
esac
""")
    podman.chmod(0o755)
    wrapper = commands / "vyatta-op-cmd-wrapper"
    wrapper.write_text("#!/bin/sh\necho 'set container name technitium memory 512'\n")
    wrapper.chmod(0o755)
    # Only platform commands and absolute paths change; all shell control flow runs.
    candidate = rendered["candidate"].replace("/config/", str(config) + "/")
    candidate = candidate.replace("tar --directory=/ ", f"tar --directory={tmp_path} ")
    candidate = candidate.replace("/opt/vyatta/bin/vyatta-op-cmd-wrapper", str(wrapper))
    candidate = candidate.replace("http://127.0.0.1:5380/api/", base)
    environment = dict(os.environ, PATH=str(commands) + os.pathsep + os.environ["PATH"])
    # External harness restart separately verifies actual disk persistence.
    for _ in range(0 if os.environ.get("TECHNITIUM_VERIFY_PERSISTED") == "1" else 2):
        run = subprocess.run(
            ["bash"], input=candidate, text=True, capture_output=True, env=environment
        )
        assert run.returncode == 0, run.stdout + run.stderr
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
        } == {ipaddress.ip_network(value) for value in rendered["acl"]}
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
        assert len(observed["reservedLeases"]) == len(scope["reserved_leases"])
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
