# Recover established Atlas authorities

Use `proxmox/atlas-infra-restore.sh KIT EXPECTED_MANIFEST_SHA256` on a **fresh
replacement Atlas**, with the original machine offline. This restores established
Vault, OpenVox, and optionally FreeIPA; it never initializes authorities or issues
certificates. Existing authority volumes, credentials, checkouts and the Git
receiver are refused. An interrupted restore leaves its work in
`/root/openvox-recovery` for inspection; it does not remove or overwrite that state
on retry.

The maintained procedure replaces the procedural files in the protected
`openvox-atlas-maintenance-acceptance-20261019T180700Z-60da9a9` evidence directory.
That evidence remains historical: its guest had ordinary numeric ownership,
only twelve authority volumes and no production IPA. This entrypoint restores
volumes **through Docker's user namespace**, including the thirteenth IPA volume
when present. It does not apply the lab's ordinary ownership to production data.

## Prepare the replacement

1. Transfer the **complete private kit** from its independent off-host location,
   retaining `0700` directories and `0600` files. Obtain the expected manifest
   SHA-256 from the separately retained backup receipt, not from the transferred
   kit. The script verifies this hash, binds the checksum list to the manifest,
   and verifies every archive before restoring anything.
2. Install Docker with its containerd image store, Compose, `ctr`, Python 3,
   `jq`, `git`, GNU tar, and the normal Atlas runtime prerequisites. IPA also
   requires `sysbox-runc`. Retain the canonical hostname `atlas.home.arpa`, service
   DNS names, and the intended IP/MAC reservation. Keep Puppet stopped throughout
   recovery. For a rehearsal, isolate DNS and all egress from production first.
3. Inspect the archived daemon and subordinate-ID settings without applying all
   of `host-config.tar.gz`. Provision the archived `userns-remap` account and its
   matching `/etc/subuid` and `/etc/subgid` ranges before starting the fresh
   Docker daemon. Do not remap an existing Docker installation. The restore
   entrypoint checks the configured mapping and its account ranges; unrelated
   subordinate-ID entries may differ. Volume data is extracted by a helper
   container, preserving the original container UID/GID, ACLs and xattrs.
4. Run a trusted copy of this repository's restore script and neighboring
   `atlas-repair-oci.py` from a temporary checkout outside the restoration targets:

   ```sh
   bash /root/recovery-tools/proxmox/atlas-infra-restore.sh \
     /root/atlas-backup REPLACE_WITH_SEPARATELY_RECORDED_MANIFEST_SHA256
   ```

The script imports the exact saved image blobs. Docker's earlier saved OCI index
omitted genuine manifests; `atlas-repair-oci.py` reconstructs that index from
manifest or configuration IDs, checks manifest digests, and requires one distinct
manifest per service. It changes only index metadata. The original archives and
production multiarch pins remain unchanged. `ctr -n moby` must address Docker's
containerd instance; inspect the private `image-import.log` if the daemon uses a
nondefault containerd socket.

Restored host configuration includes **SSH host keys, native enrollment, issuer
checkpoints and website private keys**. The replacement therefore inherits the
old host's identity. Bind inputs use the same host remapping as the archived
installation. Current backups also include `/opt/docker/.env` and existing
`/opt/docker/<project>/.env` files; older kits require these to be recovered
separately. Project secret files outside the authority kit still need their
ordinary application backups. Do not put those values into tracked configuration.

## Start and validate

The script writes `/root/openvox-recovery/infra.json` and, when IPA was backed up,
`ipa.json`. These are protected, temporary recovery models rendered from the
archived source. They select only restored authorities and genuine archived image
aliases, allowing startup without a registry, build, deployment or authority
initialization:

```sh
docker compose -p infra -f /root/openvox-recovery/infra.json \
  up -d --no-build --pull never --wait --wait-timeout 300
# IPA's external ingress network must exist, as on the original Atlas:
docker network inspect traefik_proxy >/dev/null 2>&1 || \
  docker network create traefik_proxy
# Only when the kit contains IPA:
docker compose -p infra -f /root/openvox-recovery/ipa.json \
  up -d --no-build --pull never --wait --wait-timeout 600
```

Check that every restored service is healthy and Vault unseals automatically.
Verify the saved root fingerprint against the served chain and the backup receipt,
OpenVox's exact DNS identity/full chain, and unchanged intermediate identities.
Run the existing scoped compiler check:

```sh
docker exec infra-openvox-1 /opt/puppetlabs/puppet/bin/ruby /infra/infra-vault-check.rb
```

For IPA, native startup uses the restored `/data` volume without custom identity
markers. Verify trusted HTTPS and LDAPS, and confirm
`ipa server-show ipa.home.arpa` with the preserved
admin credential. Do not print that credential or root tokens into transcripts.
Verify native enrollment and application website identities before restarting
Puppet. Restore the normal prerequisite packages/trust store and reload systemd
before enabling saved renewal timers. A successful archive import alone is not
identity acceptance.

## Return to ordinary maintenance

Keep the verified kit and recovery models until acceptance is complete. The
archive source intentionally has no `.git` directory. Reattach the control
checkout to the restored bare receiver without changing its archived files:

```sh
source=/opt/docker/puppet-control-repo
receiver=/opt/git/openvox/control.git
revision=$(jq -r .control_revision /root/atlas-backup/manifest.json)
git -C "$source" init --initial-branch=production
git -C "$source" remote add origin "$receiver"
git -C "$source" fetch origin '+refs/heads/*:refs/remotes/origin/*'
git -C "$source" reset --mixed "$revision"
git -C "$source" diff --exit-code
```

The pinned baseline checkout also needs its Git metadata reattached from its
ordinary trusted upstream at `baseline_revision` before Git-based maintenance.
Restore the repository's normal remotes before future publication. Use the
maintained ordinary Compose entrypoints after local images can satisfy their
original immutable references. The recovery models deliberately reference the
archived architecture manifest when a saved multiarch index is absent; do not
forge a multiarch digest or replace a production pin with that child digest.
Keep the recovered containers running while acquiring/verifying the original
references, then switch back to ordinary configuration. See
[ordinary service configuration](atlas-services.md) for canonical environment
inputs.

The 2026-10-19 evidence demonstrated independent archive restoration, identity
preservation and reboot of the twelve-volume isolated predecessor. The maintained
script adds production mapping and optional IPA handling, covered by local checks;
it has not been run as a full replacement-host production restore.
