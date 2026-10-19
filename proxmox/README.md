# Proxmox VM provisioning

These scripts are the versioned entry points for building the Ubuntu template,
Docker workload VMs, and desktop VMs. This runbook describes their current
behavior. The [migration plan](../docs/26.04-migration-plan.md) tracks the OpenVox
and Ubuntu 26.04 changes and acceptance work still required.

## Prerequisites

Run on the target Proxmox host as root, with this repository checked out. The
[host setup notes](../docs/02-proxmox-setup.md) are not yet a complete host rebuild
procedure. Before provisioning, provide:

- Proxmox `qm` and `pvesh`, DHCP on bridge `vmbr0`, working DNS and outbound package
  repository access for the host and guests.
- Storage `local-zfs` and storage `local` with snippets enabled; existing writable
  directories `/var/lib/vz/snippets` and `/var/lib/vz/template/iso`.
- Host commands `curl`, `jq`, `envsubst` (gettext), `wget`, and standard shell
  utilities; `/usr/share/dict/words` for generated VM names.
- Root's `~/.ssh/id_ed25519.pub`; template creation embeds this key and Docker
  provisioning sets it explicitly. Desktop clones inherit the template's user/key.
- `uv` for desktop provisioning, with Python 3.14 or newer available to it. The
  launcher uses the repository's frozen dependency lock.
- A working Puppet server and Vault enrollment policy for either guest flow, plus
  the DNS, FreeIPA, and other services required by the assigned Puppet catalog.
  See [Puppet enrollment](../docs/puppet-enrollment.md).

Defaults in [config.sh](config.sh) are domain `home.arpa`, Puppet server
`docker.home.arpa`, template ID `9000`, storage `local-zfs`, and snippet storage
`local`. Both guest flows clone an existing template; build it first if absent.

## Inputs

All three entry points require a `.env` file in the **current working directory**,
even when required values are already exported. The root
[`.env.example`](../.env.example) covers only part of these inputs:

| Input | Required by | Meaning |
| --- | --- | --- |
| `LOG_DIR` | All three | Host directory receiving `cloud-init-output.log`; create it before template creation |
| `VAULT_ADDR` | Docker, desktop | Reachable Vault URL |
| `VAULT_TOKEN` | Docker, desktop | Credential allowed to issue the enrollment token described below |
| `VAULT_CACERT` | Optional for Docker, desktop | Host path to a trusted Vault CA certificate |
| `OPENVOX_CA_CERT` | Docker, desktop and native host enrollment | Operator-authenticated public OpenVox CA bundle to preseed before enrollment |
| `IPV6_PREFIX` | Docker | IPv6 prefix used to construct Docker address pools |

Vault must allow `auth/token/create-orphan` for a single-use, two-hour service
token with only the `puppet-enrollment` policy and the guest certname as metadata.
Keep credentials in local files or secret storage, outside version control.

Native installation now uses the signed OpenVox 24001 archive on Ubuntu
24.04/26.04 or Debian 13 amd64, with authenticated archive key and OS-specific
preferences. Puppet Core/Forge credentials are no longer installation inputs.
The shared installer leaves the service masked until `mldsa44`, native TLS policy
and authenticated CA trust are present. Existing trust or RSA identities are not
automatically replaced. See the [new-chain setup](../docs/openvox-container-migration-plan.md)
for commands and remaining guest-level verification.

The shell scripts read `.env` through `grep`, `xargs`, and `export`; use simple
`KEY=value` values without whitespace, quoting, inline comments, or expansion.
Desktop uses a different parser: one assignment per line, quoted values supported,
literal values with no shell expansion. Both load shared defaults before `.env`,
so `.env` can override them. Template creation nevertheless hardcodes ID `9000`
and the Noble image/name in its own script.

## Current commands and behavior

Run these commands from the directory containing the intended `.env`, replacing
the repository path. They are separate entry points, not a command sequence to
run blindly on an existing deployment.

```bash
# Build the current Ubuntu 24.04 / Puppet template, ID 9000.
bash /path/to/puppet-control-repo/proxmox/ubuntu-server-template.sh

# Create a Docker workload VM, using Proxmox's next available VM ID.
bash /path/to/puppet-control-repo/proxmox/docker-server.sh -n app1 -c 8 -m 16384 -d 50G

# Create a desktop VM, using Proxmox's next available VM ID.
bash /path/to/puppet-control-repo/proxmox/desktop.sh -l a -c 8 -m 8000 -d 128G
```

Docker accepts `-n NAME` or chooses a dictionary name, optionally restricted by
`-l LETTER`. Its IPv6 pool construction uses the first hostname character as a
hex digit; choose a name beginning with `a`–`f` (or use `-l a`) for this current
implementation. Desktop chooses a dictionary name and accepts `-l LETTER`.
Both accept `-i VMID`; omitting it asks Proxmox for the next ID. The resource
values above are their current defaults.

**An existing VM at the selected ID is stopped and destroyed.** The template
builder also destroys existing ID `9000`. These commands are not safe reruns
against an existing machine. Safe parallel template/VM creation remains pending
[#15](https://github.com/Enucatl/puppet-control-repo/issues/15); use unused IDs and
preserve old VMs for migration rollback. The current template builder cannot
select a separate template ID through its CLI.

The template downloads `noble/current` only when its cached image is absent and
installs current repository packages, so its result depends on cache age and
upstream repositories. It has no image checksum verification or fixed image
revision. It embeds Puppet Core repository authentication; restrict access to
the resulting template and clones.

Guest provisioning writes cloud-init, clones the template, enrolls the guest,
and waits for cloud-init completion. Desktop reboots afterward. Both append
guest logs to `LOG_DIR/cloud-init-output.log`; a cloud-init wait can continue
indefinitely if the guest agent never becomes reachable. Completion is not yet
evidence of a second clean Puppet run or application acceptance.

## Control-plane recovery and acceptance still required

`docker-server.sh` provisions an **enrolled Docker workload host**. It needs Vault
to issue a token before VM creation and Puppet to configure the guest. It does
not establish the initial Puppet server, restore Vault/FreeIPA state, or recover
the central `docker.home.arpa` host from scratch. The dependency order, required
secret/backups inventory, and cold-start recovery procedure remain tracked in
[#18](https://github.com/Enucatl/puppet-control-repo/issues/18).

For migration acceptance, record a disposable template build and both guest
flows, SSH and guest-agent access, enrollment, a second clean Puppet run,
role-specific service checks, and preservation of the old VMs. Record the image,
package versions, configuration revision, and required external state. These
checks have not been performed as part of writing this runbook.
