# Agent Operations

Common operations performed in this repo with Claude Code.

---

## Add a WireGuard peer and generate their client config

1. Add the peer to `provisioning/host_vars/router.yml` under `wg_peers`:
   ```yaml
   - name: "username"
     pubkey: "<peer public key>"
     ip: "10.0.200.X/32"
     ipv6_host: "X"
   ```
   Assign the next available host number (currently: phone=2, riccardo=3).

2. Run the router provisioning playbook to apply the change to VyOS:
   ```bash
   cd provisioning
   uv run ansible-playbook -i inventory/router.yml router.yml --vault-password-file vars/password
   ```

3. Generate a client `.conf` file for NetworkManager import:
   ```ini
   [Interface]
   PrivateKey = <PEER_PRIVATE_KEY>
   Address = 10.0.200.X/32, $(vault kv get -field ipv6-prefix kv/puppet):c8::X/128
   DNS = 10.0.0.1, $(vault kv get -field ipv6-prefix kv/puppet)::1

   [Peer]
   PublicKey = $(vault kv get -field wg_server_private_key kv/puppet | wg pubkey)
   Endpoint = vpn.enucatl.com:51820
   AllowedIPs = 0.0.0.0/0, ::/0
   PersistentKeepalive = 15
   ```
   - Use `AllowedIPs = 0.0.0.0/0, ::/0` for full tunnel (all traffic via VPN).
   - Use `AllowedIPs = 10.0.0.0/24, 10.0.10.0/24, 10.0.200.0/24, $(vault kv get -field ipv6-prefix kv/puppet):c8::/64` for split tunnel (LAN only).
   - The peer fills in their own `PrivateKey` (the one matching the pubkey registered on the server).

4. The peer imports the file in NetworkManager:
   ```bash
   nmcli connection import type wireguard file riccardo.conf
   ```
   Or via GUI: Settings → Network → VPN → Import from file.
