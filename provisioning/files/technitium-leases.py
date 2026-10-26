#!/usr/bin/env python3
"""Merge DHCP ownership into durable reservations for cutover and rollback.

CLI reads --scopes (a JSON array), optional --dnsmasq (lease file), --holds
(previous output or its holds array), --hosts (static DNS host declarations),
and --technitium (leases/list API response).
It prints JSON with holds, per-scope reserved_leases API strings, and a dnsmasq
configuration overlay. Callers must persist holds before activating DHCP.
Holds deliberately require explicit reconciliation before removal.
"""

import argparse
from datetime import datetime, timezone
import ipaddress
import json
from pathlib import Path
import re
import sys
import time


def merge(
    scopes: list[dict],
    dnsmasq: str,
    holds: list[dict],
    technitium: dict | None,
    now: int,
    hosts: list[dict] | None = None,
) -> dict:
    """Validate lease ownership and return reservations for both DHCP servers."""
    networks = {}
    for scope in scopes:
        name = scope["name"]
        router = ipaddress.IPv4Interface(scope["router"])
        start, end = map(ipaddress.IPv4Address, (scope["start"], scope["end"]))
        if (
            name in networks
            or not router.ip < start <= end < router.network.broadcast_address
            or start not in router.network
            or end not in router.network
        ):
            raise ValueError(f"Invalid scope bounds: {name}")
        if any(router.network.overlaps(old[0].network) for old in networks.values()):
            raise ValueError(f"Overlapping scope: {name}")
        networks[name] = (router, start, end)

    domains = {
        scope["name"]: scope.get("domain", "").lower().rstrip(".") for scope in scopes
    }
    dns_owners = {}
    for host in hosts or []:
        for domain in set(domains.values()):
            fqdn = host["name"].lower().rstrip(".") + ("." + domain if domain else "")
            dns_owners[fqdn] = {
                str(ipaddress.ip_address(address)) for address in host["addresses"]
            }

    owners = {}
    macs = {}
    reservations = {scope["name"]: [] for scope in scopes}
    durable = {}

    def add(
        row: dict, declared: bool = False, comment: str = "Lease continuity"
    ) -> None:
        """Check one owner, retaining a durable hold for imported leases."""
        address = str(ipaddress.IPv4Address(row["address"]))
        ip = ipaddress.IPv4Address(address)
        mac = row["mac"].lower().replace("-", ":")
        if not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", mac):
            raise ValueError(f"Invalid MAC: {mac}")
        if int(mac[:2], 16) & 1 or mac == "00:00:00:00:00:00":
            raise ValueError(f"Invalid unicast MAC: {mac}")
        matches = [
            name
            for name, (router, _, end) in networks.items()
            if ip in router.network and router.ip < ip <= end
        ]
        if len(matches) != 1 or row.get("scope", matches[0]) != matches[0]:
            raise ValueError(f"Address outside managed scope: {address}")
        name = matches[0]
        hostname = row.get("hostname") or ""
        if hostname == "*":
            hostname = ""
        if hostname and not re.fullmatch(r"[A-Za-z0-9_.-]+", hostname):
            raise ValueError(f"Invalid hostname: {hostname}")
        if hostname:
            hostname = hostname.lower().rstrip(".")
            domain = domains[name]
            if "." in hostname and (not domain or not hostname.endswith("." + domain)):
                raise ValueError(f"Hostname outside scope domain: {hostname}")
            fqdn = (
                hostname
                if "." in hostname
                else hostname + ("." + domain if domain else "")
            )
            if fqdn in dns_owners and address not in dns_owners[fqdn]:
                raise ValueError(f"Conflicting DNS owner for {fqdn}")
            dns_owners.setdefault(fqdn, {address})
        if any(c in comment for c in "|\r\n"):
            raise ValueError("Invalid reservation comment")
        if address in owners and owners[address] != mac:
            raise ValueError(f"Conflicting owners for {address}")
        key = mac
        if key in macs and macs[key] != address:
            raise ValueError(f"Conflicting addresses for {mac}")
        if address not in owners:
            reservations[name].append("|".join((hostname, mac, address, comment)))
        owners[address] = mac
        macs[key] = address
        if not declared:
            expiry = row["expires"]
            if type(expiry) is not int or expiry < 0:
                raise ValueError("Invalid lease expiry")
            previous = durable.get(address)
            if previous:
                expiry = (
                    0
                    if 0 in (expiry, previous["expires"])
                    else max(expiry, previous["expires"])
                )
            durable[address] = dict(
                scope=name,
                mac=mac,
                address=address,
                hostname=hostname,
                expires=expiry,
                sources=sorted(
                    set(row.get("sources", ["previous-hold"]))
                    | set(previous["sources"] if previous else [])
                ),
            )

    for scope in scopes:
        for reservation in scope["reserved_leases"]:
            hostname, mac, address, comment = reservation.split("|")
            add(
                dict(scope=scope["name"], hostname=hostname, mac=mac, address=address),
                declared=True,
                comment=comment,
            )
    declared_addresses = set(owners)
    if isinstance(holds, dict):
        holds = holds["holds"]
    for row in holds:
        add(row)
    for line in dnsmasq.splitlines():
        if not line.strip():
            continue
        expiry, mac, address, hostname, _client_id = line.split()
        expiry = int(expiry)
        if expiry < 0:
            raise ValueError("Invalid lease expiry")
        if expiry == 0 or expiry > now:
            add(
                dict(
                    mac=mac,
                    address=address,
                    hostname=hostname,
                    expires=expiry,
                    sources=["dnsmasq"],
                )
            )
    if technitium is not None:
        if technitium["status"] != "ok":
            raise ValueError("Technitium lease export failed")
        for row in technitium["response"]["leases"]:
            stamp = row["leaseExpires"]
            try:
                expires = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            except ValueError:
                expires = datetime.strptime(stamp, "%m/%d/%Y %H:%M:%S")
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            expiry = int(expires.timestamp())
            if expiry > now:
                add(
                    dict(
                        scope=row["scope"],
                        mac=row["hardwareAddress"],
                        address=row["address"],
                        hostname=row["hostName"],
                        expires=expiry,
                        sources=["technitium"],
                    )
                )
    return {
        "holds": sorted(
            durable.values(), key=lambda row: (row["scope"], row["address"])
        ),
        "reserved_leases": {
            name: "|".join(rows) for name, rows in reservations.items()
        },
        "dnsmasq": "".join(
            f"dhcp-host={mac},{address}\n"
            for address, mac in sorted(owners.items())
            if address not in declared_addresses
        ),
    }


def main() -> None:
    """Read snapshots and emit a validated merge without modifying input files."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scopes", type=Path, required=True)
    parser.add_argument("--dnsmasq", type=Path)
    parser.add_argument("--holds", type=Path)
    parser.add_argument("--hosts", type=Path)
    parser.add_argument("--technitium", type=Path)
    args = parser.parse_args()
    try:
        result = merge(
            json.loads(args.scopes.read_text()),
            args.dnsmasq.read_text() if args.dnsmasq else "",
            json.loads(args.holds.read_text()) if args.holds else [],
            json.loads(args.technitium.read_text()) if args.technitium else None,
            int(time.time()),
            json.loads(args.hosts.read_text()) if args.hosts else None,
        )
    except (ValueError, KeyError, TypeError, OSError) as error:
        sys.exit(f"Lease continuity validation failed: {error}")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
