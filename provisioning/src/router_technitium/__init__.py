"""Configure Technitium DNS and recover DHCP scopes on late interfaces."""

import argparse
import fcntl
import ipaddress
import json
from pathlib import Path
import subprocess
import syslog
import time

from .api import Api


def configure(api: Api, config: dict) -> None:
    """Refresh managed DNS settings and records, preserving DHCP and other records."""
    api("settings/set", **config["settings"])
    zones = {zone["name"] for zone in api("zones/list")["response"]["zones"]}
    domain = config["domain"]
    for zone in [domain, config["intranet"], *config["reverse_zones"]]:
        if zone not in zones:
            api("zones/create", zone=zone, type="Primary")
        api(
            "zones/options/set",
            zone=zone,
            queryAccess="UseSpecifiedNetworkACL",
            queryAccessNetworkACL=",".join(config["local_query_acl"]),
            zoneTransfer="Deny",
        )
    api(
        "zones/records/add",
        zone=config["intranet"],
        domain=config["intranet"],
        type="DNAME",
        dname=domain,
        ttl=300,
        overwrite=True,
    )
    for host in config["hosts"]:
        for version, record_type in [(4, "A"), (6, "AAAA")]:
            addresses = [
                address
                for address in host["addresses"]
                if ipaddress.ip_address(address).version == version
            ]
            for index, address in enumerate(addresses):
                record = dict(
                    zone=domain,
                    type=record_type,
                    ipAddress=address,
                    ttl=300,
                    overwrite=index == 0,
                )
                api(
                    "zones/records/add",
                    domain=f"{host['name']}.{domain}",
                    ptr=host.get("ptr", False),
                    **record,
                )
                if host.get("wildcard"):
                    api(
                        "zones/records/add",
                        domain=f"*.{host['name']}.{domain}",
                        **record,
                    )
    api(
        "zones/records/add",
        zone=domain,
        domain=f"_apt_proxy._tcp.{domain}",
        type="SRV",
        priority=0,
        weight=0,
        port=3142,
        target=f"apt-cacher-ng.{domain}",
        ttl=10,
        overwrite=True,
    )
    for domain in ["icu", "version.bind", "id.server", "hostname.bind"]:
        api("blocked/add", domain=domain)


def bootstrap(api: Api, config: dict) -> None:
    """Create initial DHCP scopes in the isolated container managed by Ansible."""
    names = {scope["name"] for scope in config["scopes"]}
    if any(
        scope["name"] in names
        for scope in api("dhcp/scopes/list")["response"]["scopes"]
    ):
        raise RuntimeError("Bootstrap cannot overwrite existing managed DHCP scopes")
    configure(api, config)
    for scope in config["scopes"]:
        router = str(ipaddress.ip_interface(scope["router"]).ip)
        days, hours = divmod(scope["lease_hours"], 24)
        api(
            "dhcp/scopes/set",
            name=scope["name"],
            startingAddress=router,
            endingAddress=scope["end"],
            subnetMask="255.255.255.0",
            exclusions=f"{router}|{ipaddress.ip_address(scope['start']) - 1}",
            leaseTimeDays=days,
            leaseTimeHours=hours,
            leaseTimeMinutes=0,
            pingCheckEnabled=True,
            domainName=scope.get("domain", ""),
            dnsUpdates="domain" in scope,
            dnsOverwriteForDynamicLease=False,
            dnsTtl=300,
            routerAddress=router,
            useThisDnsServer=False,
            dnsServers=config["dns"],
            serverAddress=scope.get("boot_server", ""),
            bootFileName=scope.get("boot_file", ""),
            reservedLeases="|".join(scope["reserved_leases"]),
        )
        api("dhcp/scopes/disable", name=scope["name"])


def enable_dhcp(api: Api, config: dict) -> None:
    """Enable the declared scopes after Ansible starts the production container."""
    names = {scope["name"] for scope in config["scopes"]}
    # The HTTP service starts before saved DHCP scopes finish loading.
    for attempt in range(30):
        loaded = {
            scope["name"] for scope in api("dhcp/scopes/list")["response"]["scopes"]
        }
        if names <= loaded:
            break
        if attempt == 29:
            raise RuntimeError("DHCP scopes did not finish loading")
        time.sleep(1)
    for scope in config["scopes"]:
        api("dhcp/scopes/enable", name=scope["name"])


def rebind(api: Api, config: dict) -> None:
    """Recover unbound DHCP scopes without changing settings or reservations."""
    interfaces = json.loads(
        subprocess.check_output(["ip", "-j", "-4", "address", "show", "up"])
    )
    addresses = {
        address["local"]
        for interface in interfaces
        if interface["operstate"] == "UP"
        for address in interface["addr_info"]
    }
    for scope in config["scopes"]:
        name = scope["name"]
        pending = Path(config["data_path"]) / f"rebind-{name}.pending"
        # Finish recovery interrupted after disabling a scope, even across reboots.
        if pending.exists():
            api("dhcp/scopes/enable", name=name)
            pending.unlink()
        scopes = api("dhcp/scopes/list")["response"]["scopes"]
        unbound = next(
            (
                row
                for row in scopes
                if row["name"] == name
                and row["enabled"]
                and row["interfaceAddress"] == "0.0.0.0"
            ),
            None,
        )
        if unbound and unbound["startingAddress"] in addresses:
            pending.touch(mode=0o600)
            api("dhcp/scopes/disable", name=name)
            api("dhcp/scopes/enable", name=name)
            pending.unlink()
            syslog.syslog(f"Technitium: rebound DHCP scope {name}")


def main() -> None:
    """Run one operation with a lock shared by configuration and scheduled recovery."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["configure", "bootstrap", "enable-dhcp", "rebind"]
    )
    parser.add_argument("config", type=Path)
    arguments = parser.parse_args()
    config = json.loads(arguments.config.read_text())
    with open("/run/lock/technitium.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            if arguments.command == "rebind":
                return
            raise RuntimeError("Another Technitium operation is running") from None
        if (
            arguments.command == "rebind"
            and subprocess.run(
                [
                    "systemctl",
                    "is-active",
                    "--quiet",
                    "vyos-container-technitium.service",
                ]
            ).returncode
        ):
            return
        password = (Path(config["data_path"]) / "admin-password").read_text().strip()
        operations = {
            "configure": configure,
            "bootstrap": bootstrap,
            "enable-dhcp": enable_dhcp,
            "rebind": rebind,
        }
        with Api(password) as api:
            operations[arguments.command](api, config)
