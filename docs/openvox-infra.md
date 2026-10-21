# OpenVox, Vault and FreeIPA on Atlas

The [root Compose file](../compose.yaml) loads the canonical infrastructure
services. OpenVox uses the accepted server image's foreground command and native
entrypoint. FreeIPA uses its published ML-DSA-compatible image directly, with
native init. Installation settings, mounts, image pins and healthchecks belong
in Compose; neither service has a custom startup wrapper.

Deploy this configuration and apply Atlas's Puppet catalog to activate its
certificate resources and remove the obsolete renewal timer units. Editing the
checkout alone does not update installed catalogs or systemd units.

## Established services

From the repository root on Atlas:

```sh
docker compose up -d --wait openvox vault vault-unsealer
docker compose --env-file /dev/null -f docker/freeipa/atlas-compose.yaml up -d --wait
```

These commands start existing state. The Python initialization commands below
are for a new authority, not ordinary startup or restoration. Compose names the
external volumes explicitly. There are no additional volume-identity markers
or startup checkpoint checks.

| Purpose | Identity / endpoint |
| --- | --- |
| Host and native Puppet agent | `atlas.home.arpa` |
| OpenVox server | `openvox.home.arpa:8140` |
| Vault | `https://vault.home.arpa:8200` |
| FreeIPA | `ipa.home.arpa`, realm `HOME.ARPA` |

Atlas's reserved IPv4 address is `10.0.0.158`, with stable ULA
`fd6b:d722:1738:0:be24:11ff:fe4e:a49e`. Atlas remains the canonical PTR.
The [router configuration](../provisioning/README.md#atlas-infrastructure-dns-and-dhcp)
contains the reservation and service records. Legacy Docker authorities keep
existing identities until their clients migrate.

## Fresh initialization

First complete the one-time [fresh Atlas Git receiver setup](../proxmox/README.md#fresh-atlas-git-receiver)
as the checkout owner. Then initialize a fresh Atlas authority from the
repository root as root:

```sh
python3 proxmox/atlas-infra.py initialize
python3 proxmox/atlas-freeipa.py initialize
```

The helpers prepare initial authorities, certificates, passwords, volumes and
code deployment. Start established services with Compose. Repeating fresh
initialization over an installed authority is refused; it is not recovery.

Vault creates its ML-DSA-44 root internally with a ten-year lifetime. Its signing
key stays in Vault storage. The service, OpenVox and FreeIPA intermediates have
five-year lifetimes. OpenVox generates its intermediate key and CSR locally and
imports the root-signed public chain. Resumable initialization checkpoints live
under each service's `secrets/bootstrap/`; they are not startup requirements.

The separate [enrollment helper](puppet-enrollment.md#atlas) installs Atlas's
native agent using accepted packages and authenticated public CA trust.
FreeIPA provisioning uses its [delegated service keytab](freeipa-provisioning.md).

## Persistent state and credentials

Runtime PEM files and recovery inputs live beside Compose, excluded from Git:

| Directory | Contents |
| --- | --- |
| `docker/vault/secrets/` | `fullchain.pem`, `key.pem`, `ca.pem`, `recovery.json`, `root-token`, `public/vault.pub.pem`, initialization checkpoints |
| `docker/freeipa/secrets/` | Shared HTTP/LDAP `fullchain.pem` and `key.pem`, `ca.pem`, `installer.json`, native installer options and initialization checkpoints |

`ca.pem` is the **public CA certificate**, used to authenticate services. Its
signing key remains inside Vault. `public/vault.pub.pem` is Vault's public TLS
key; the unsealer mounts that public subdirectory and its own unseal share,
without access to the TLS private key. Passwords, tokens and private keys retain
service-specific ownership and restrictive modes.

The old `/root/openvox-infra` directory mixed credentials with application
staging, rollback copies and migration receipts. Current services, enrollment
and certificate refreshes use the inputs above. Completed migration copies and
historical receipts are not runtime dependencies.

| External volume | Contents |
| --- | --- |
| `infra_openvox_ssl` | OpenVox server identity and trust |
| `infra_openvox_ca` | Intermediate key, signed certificates, CRLs and requests |
| `infra_openvox_code` | Deployed production/dev environments |
| `infra_openvox_data` | Server data and agent state |
| `infra_openvox_gems` | Installed JRuby gems |
| `infra_openvox_git` | Deployment status; the host bare repository is a separate read-only bind |
| `infra_openvox_r10k_cache` | Deployment cache |
| `infra_openvox_trust` | Public root and intermediate trust |
| `infra_vault_data`, `infra_vault_logs`, `infra_vault_unseal` | Vault storage, logs and unseal share |
| `infra_ipa_data` | FreeIPA's installed `/data` state |

External volumes survive `docker compose down -v`; backup and restore still
need their contents and the Compose-adjacent secrets.

## Automated Vault and IPA transport renewal

Ordinary Puppet runs manage these PEM files through the same `vault_cert`
resource used by application certificates:

| Resource | Issuance endpoint | Lifetime / renewal window |
| --- | --- | --- |
| `Vault_cert[vault-transport]` | `pki_int/issue/vault-transport` | 30 days / 10 days |
| `Vault_cert[ipa-transport]` | `pki_freeipa/issue/ipa-transport` | 90 days / 30 days |

Atlas's native agent authenticates as `atlas-transport`, permitting only these
exact issuance roles. Initialization sets the policy with the protected root
token; ordinary renewal uses the agent certificate. Puppet's standard provider
retains its own certificate metadata.

`profile::atlas::certificates` applies the PEMs with
`proxmox/atlas-apply-certificates.py`. Vault's refresh publishes the corresponding
public unsealer pin before signaling Vault to reload TLS. FreeIPA's refresh
imports the shared certificate into both native HTTP and Directory Server
stores, then restarts those services. It reads the Directory Manager password
from `docker/freeipa/secrets/installer.json`; credentials travel over stdin to
private temporary container files, removed after import.

Each apply resource compares the live TLS certificate to the canonical PEM.
A failed import or reload retries on the next Puppet run using those same PEMs.
There is no separate transport timer, custom certificate JSON or pending-renewal
state. If Vault's leaf expires while Puppet is inactive, recover a valid leaf
before authenticated issuance can resume.

### Activating the replacement on an existing Atlas

An existing deployment needs the new scoped `atlas-transport` login before its
new Puppet certificate resources can authenticate. With the canonical
`docker/vault/secrets/root-token` and `ca.pem` already present, configure only
that access from the repository root:

```sh
sudo env OPENSSL_CONF="$PWD/docker/openvox/openssl.cnf" python3 - <<'PY'
import os
import runpy

os.umask(0o077)
controller = runpy.run_path("proxmox/atlas-infra.py")
controller["STATE"].mkdir(mode=0o700, parents=True, exist_ok=True)
with controller["maintenance"]():
    chain = controller["run"](
        controller["DOCKER"]
        + ["exec", "infra-openvox-1", "cat", "/trust/openvox-chain.pem"]
    )
    controller["configure_transport_access"](chain)
PY
```

This updates the restricted policy and certificate-auth role without issuing
certificates or initializing authorities. Publish and deploy the updated
catalog through the normal Git flow, then apply the Compose configuration under
the shared maintenance lock and run the ordinary Atlas Puppet agent:

```sh
sudo flock -w 300 /run/lock/atlas-openvox-maintenance.lock \
  docker --host unix:///var/run/docker.sock compose --env-file /dev/null \
  -f /opt/docker/puppet-control-repo/compose.yaml \
  up -d --no-deps --no-build --pull never --wait --wait-timeout 600 \
  openvox vault vault-unsealer
sudo /opt/puppetlabs/bin/puppet agent --test
```

Its catalog activates certificate management and stops, disables and removes
the obsolete renewal timer units. Do not rerun
`initialize` on established services. Until that deployment and agent run finish,
repository edits alone have not replaced the installed renewal jobs.

## Native OpenVox certificate renewal

The server leaf lasts 60 days. Puppet's native renewal window is 30 days;
`profile::atlas::certificates` checks the container's server identity during
ordinary Atlas agent runs. When due, the apply helper invokes native
`puppet ssl renew_cert --if-expiring-in 30d` in the running container. The
webserver's separate `ssl-cert-chain` setting supplies the intermediate and
root, so the native CLI writes its leaf normally.

If the on-disk leaf differs from the listener, the helper restarts OpenVox and
checks the authenticated live certificate. A later Puppet run also retries an
interrupted reload. Vault and FreeIPA keep running. There is no custom OpenVox
renewal timer or certificate-bundle rewrite job. CA renewal remains an operator
operation before issuer expiry.

## Code deployment

The host bare repository is `/opt/git/openvox/control.git`, bound read-only at
`/git/control.git`. The remapped OpenVox user (`999:999` in the container,
`100999:100999` on Atlas) reads that public code. Writable deployment status
lives on the separate `/git` volume. r10k reads `file:///git/control.git`, with
image Git trust scoped to that repository.

Fresh initialization deploys production/dev, generates types and installs the
host post-receive dispatcher. The code volume uses `nocopy`, preventing Docker
from seeding it with the image's template environment. No template-migration
helper runs during startup.

Later production/dev receives run r10k, generate types and invalidate compiler
caches. After an ordinary Git push, check `/git/deploy.status` is `0` and
`/git/deploy.commit` matches the intended revision; accepting a ref alone does
not prove its hook succeeded. After correcting a failed deployment, replay the
current ref-update input to the managed hook. An unchanged push does not replay
it. See the [publishing instructions](../proxmox/README.md#atlas-production-publishing-and-legacy-separation).

The deployed `hiera.yaml` uses `vault.home.arpa` and the host system bundle,
mounted read-only at `/etc/ssl/certs/ca-certificates.crt`. Initial authority setup
installs the authenticated root into that bundle before deploying Hiera.
The compiler's `puppet-server` Vault login admits exactly `openvox.home.arpa`
and reads only `kv/data/puppet`. Native agent transport issuance uses its own
scoped role. Hiera fails compilation when its authenticated Vault lookup fails.

## TLS and service configuration

The accepted `24001` image is pinned in `docker/openvox/infra.Dockerfile`; the
derived image adds Git and r10k 5.0.3. It keeps the native entrypoint and
foreground command. Compose declares UID/GID 999, a read-only image, dropped
capabilities, JVM limits and writable runtime tmpfs. `/tmp` permits executable
mappings for JRuby's native library extraction. Tini remains PID 1.

OpenVox publishes TLS directly on TCP 8140. Its listener and native/JVM clients
use TLS 1.3, ML-DSA-44 signatures and `X25519MLKEM768`. Vault's pinned image and
`docker/openvox/infra-vault.hcl` are in canonical Compose, with `secrets/` bound
read-only at `/certificates`. It reads `fullchain.pem`, `key.pem` and `ca.pem`.
Client certificates are optional at TLS connection time for token enrollment;
Vault's certificate-auth method authorizes them separately.

For `/sys/health` and `/sys/unseal`, the unsealer authenticates Vault's exact
locally pinned public TLS key. Those bootstrap requests require
`--pinnedpubkey` and work while Vault cannot publish CRLs or renew an expired
leaf. Application clients retain full chain, hostname and expiry verification.
Any TLS key change publishes its public pin before switching the listener.

The deployment retains standard OpenVox/Vault CRL behavior and
`certificate_revocation=leaf`; it adds no custom CRL publisher or refresh timer.

## Backups and recovery

Use the normal [Proxmox/PBS workflow](atlas-recovery.md), retaining Docker
volumes, service secrets, host identities and subordinate-ID mappings. Restore
established state and start it with Compose. Do not initialize a replacement
authority over restored data.

The weekly `vault-snapshot.timer` and `docker/vault/scripts/backup.sh` remain;
their `/scratch/backup/vault` exports continue through Backrest. Custom
full-authority archive/restore tools and migration clone tools are retired.

## Maintenance release and independent recovery

The [initial activation](atlas-infra-results.json),
[native enrollment](atlas-enrollment-results.json),
[OpenVox renewal](atlas-openvox-renewal-results.json) and
[maintenance acceptance](atlas-maintenance-results.json) records describe
earlier measured deployments and protected off-host kits. They retain
historical identity and recovery evidence; deleted wrapper commands and old
`/root/openvox-infra` paths are not current operating instructions.
