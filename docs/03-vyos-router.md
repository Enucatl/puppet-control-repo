# VyOS configuration with Salt box (single /64 prefix + CGNAT)

## Interfaces

1. WAN (PCIe Passthrough):
- ipv4 static address set as DMZ on the router (no bridge mode available). 192.168.1.2
- ipv6 no global address, only a local address (fe80::...)

2. LAN (connected to proxmox virtual bridge)
- ipv4 static address 10.0.0.1
- fixed ipv6 address {{ ipv6_prefix }}::2

You can't assign a global ipv6 to the WAN interface, otherwise the routing table would not know where to send packets, because we have only a /64 (not compliant btw, thanks Salt).

Routes have to be manually configured to send 0.0.0.0/0 and ::0/0 to the local address of the ISP router, through the WAN interface.

Also set ndp-proxy is necessary on the WAN interface so that when packets come back from the internet, the ISP router will ask "who has this address?" and VyOS can answer.

## Adguard Home
Provides a simple DNS and DHCP configuration. IPv6 only through address autoconfiguration (SLAAC).
