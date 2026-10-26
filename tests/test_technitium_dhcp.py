"""Perform a DHCP exchange entirely inside the disposable server namespace."""

import json
import os
from pathlib import Path
import signal
import subprocess
import time
import urllib.parse
import urllib.request

import pytest
import yaml

from test_technitium_leases import leases


@pytest.mark.parametrize("interface_down", [False, True])
def test_dhcp_ack_and_renewal(tmp_path: Path, interface_down: bool) -> None:
    """A held MAC renews after normal startup or rebinding a late interface."""
    base = os.environ.get("TECHNITIUM_API_URL")
    if not base or os.environ.get("TECHNITIUM_VERIFY_PERSISTED") == "1":
        pytest.skip("Requires the isolated initial-run namespace")

    def api(path: str, **parameters: object) -> dict:
        request = urllib.request.Request(
            base + path, data=urllib.parse.urlencode(parameters).encode()
        )
        with urllib.request.urlopen(request, timeout=45) as response:
            result = json.load(response)
        assert result["status"] == "ok", result
        return result

    token = api("user/login", user="admin", **{"pass": "test-password"})["token"]
    subprocess.run(
        [
            "ip",
            "link",
            "add",
            "dhcp-server",
            "type",
            "veth",
            "peer",
            "name",
            "dhcp-client",
        ],
        check=True,
    )
    process = None
    try:
        for arguments in [
            ["address", "add", "198.19.0.1/24", "dev", "dhcp-server"],
            ["link", "set", "dhcp-server", "up"],
            ["link", "set", "dhcp-client", "address", "02:00:00:aa:bb:dd"],
            ["link", "set", "dhcp-client", "up"],
        ]:
            if interface_down and arguments == ["link", "set", "dhcp-server", "up"]:
                continue
            subprocess.run(["ip", *arguments], check=True)
        api(
            "dhcp/scopes/set",
            token=token,
            name="Handshake",
            startingAddress="198.19.0.1",
            exclusions="198.19.0.1|198.19.0.99",
            endingAddress="198.19.0.110",
            subnetMask="255.255.255.0",
            routerAddress="198.19.0.1",
            useThisDnsServer="false",
            dnsServers="10.0.0.1",
            pingCheckEnabled="false",
            reservedLeases="held|02:00:00:aa:bb:dd|198.19.0.11|Continuity test",
        )
        script = tmp_path / "client.sh"
        events = tmp_path / "events"
        script.write_text(
            '#!/bin/sh\ncase "$1" in bound|renew) '
            f'printf "%s %s %s %s\\n" "$1" "$ip" "$router" "$dns" >> {events};; esac\n'
        )
        script.chmod(0o755)
        if interface_down:
            scope = next(
                row
                for row in api("dhcp/scopes/list", token=token)["response"]["scopes"]
                if row["name"] == "Handshake"
            )
            print("Scope before link-up:", json.dumps(scope, sort_keys=True))
            assert scope["interfaceAddress"] == "0.0.0.0"
            subprocess.run(["ip", "link", "set", "dhcp-server", "up"], check=True)
            probe = subprocess.run(
                [
                    "busybox",
                    "udhcpc",
                    "-f",
                    "-n",
                    "-q",
                    "-i",
                    "dhcp-client",
                    "-s",
                    str(script),
                    "-t",
                    "3",
                    "-T",
                    "2",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            print("DHCP after link-up:", probe.returncode, probe.stdout, probe.stderr)
            print(
                "Scope after link-up:",
                json.dumps(
                    api("dhcp/scopes/list", token=token)["response"], sort_keys=True
                ),
            )
            assert probe.returncode != 0
            candidate = tmp_path / "technitium-candidate"
            candidate.mkdir()
            (candidate / "admin-password").write_text("test-password")
            (candidate / "cutover-active").touch()
            helper = tmp_path / "rebind.sh"
            play = [
                {
                    "hosts": "localhost",
                    "connection": "local",
                    "gather_facts": False,
                    "vars": {
                        "technitium_data_path": str(tmp_path / "technitium"),
                        "technitium_dhcp_scopes": [{"name": "Handshake"}],
                    },
                    "tasks": [
                        {
                            "ansible.builtin.template": {
                                "src": str(
                                    Path(__file__).parents[1]
                                    / "provisioning/templates/app_configs/technitium-rebind.sh.j2"
                                ),
                                "dest": str(helper),
                            }
                        }
                    ],
                }
            ]
            play_path = tmp_path / "render.yml"
            play_path.write_text(yaml.safe_dump(play))
            subprocess.run(
                ["ansible-playbook", "-i", "localhost,", str(play_path)],
                check=True,
                capture_output=True,
            )
            rebind_script = helper.read_text().replace(
                "/run/lock/technitium-cutover.lock", str(tmp_path / "rebind.lock")
            )
            rebind_script = (
                'podman() { shift 2; "$@"; }; systemctl() { return 0; }; logger() { :; }\n'
                + rebind_script
            )
            before = api("dhcp/scopes/get", token=token, name="Handshake")["response"]
            subprocess.run(
                ["bash"],
                input=rebind_script,
                text=True,
                check=True,
                capture_output=True,
            )
            assert (
                api("dhcp/scopes/get", token=token, name="Handshake")["response"]
                == before
            )
            subprocess.run(
                ["bash"],
                input=rebind_script,
                text=True,
                check=True,
                capture_output=True,
            )
            assert not list(candidate.glob("*.pending"))
            # Rollback removes this marker; scheduled recovery must then be inert.
            (candidate / "cutover-active").unlink()
            subprocess.run(
                ["bash"],
                input=rebind_script.replace(
                    "systemctl() { return 0; }", "systemctl() { exit 99; }"
                ),
                text=True,
                check=True,
            )

            assert (
                next(
                    row
                    for row in api("dhcp/scopes/list", token=token)["response"][
                        "scopes"
                    ]
                    if row["name"] == "Handshake"
                )["interfaceAddress"]
                == "198.19.0.1"
            )
        with (tmp_path / "client.log").open("w+") as log:
            process = subprocess.Popen(
                [
                    "busybox",
                    "udhcpc",
                    "-f",
                    "-n",
                    "-i",
                    "dhcp-client",
                    "-s",
                    str(script),
                    "-t",
                    "3",
                    "-T",
                    "2",
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            for count in [1, 2]:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    rows = events.read_text().splitlines() if events.exists() else []
                    if len(rows) >= count:
                        break
                    if process.poll() is not None:
                        break
                    time.sleep(0.1)
                log.seek(0)
                assert len(rows) >= count, log.read()
                assert rows[count - 1].split()[1:] == [
                    "198.19.0.11",
                    "198.19.0.1",
                    "10.0.0.1",
                ]
                if count == 1:
                    subprocess.run(
                        [
                            "ip",
                            "address",
                            "add",
                            "198.19.0.11/24",
                            "dev",
                            "dhcp-client",
                        ],
                        check=True,
                    )
                    time.sleep(0.2)
                    process.send_signal(signal.SIGUSR1)
            assert rows[0].startswith("bound ")
            log.seek(0)
            output = log.read()
            assert "sending renew" in output, output
            exported = api("dhcp/leases/list", token=token)
            exported["response"]["leases"] = [
                row
                for row in exported["response"]["leases"]
                if row["scope"] == "Handshake"
            ]
            merged = leases.merge(
                [
                    {
                        "name": "Handshake",
                        "router": "198.19.0.1/24",
                        "start": "198.19.0.100",
                        "end": "198.19.0.110",
                        "reserved_leases": [],
                    }
                ],
                "",
                [],
                exported,
                int(time.time()),
            )
            assert merged["dnsmasq"] == "dhcp-host=02:00:00:aa:bb:dd,198.19.0.11\n"
            if not interface_down:
                process.terminate()
                process.wait(timeout=5)
                for state in ["down", "up"]:
                    subprocess.run(
                        ["ip", "link", "set", "dhcp-server", state], check=True
                    )
                retry = subprocess.run(
                    [
                        "busybox",
                        "udhcpc",
                        "-f",
                        "-n",
                        "-q",
                        "-i",
                        "dhcp-client",
                        "-s",
                        str(script),
                        "-t",
                        "3",
                        "-T",
                        "2",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                assert retry.returncode == 0, retry.stdout + retry.stderr
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        api("dhcp/scopes/delete", token=token, name="Handshake")
        subprocess.run(["ip", "link", "delete", "dhcp-server"], check=True)
