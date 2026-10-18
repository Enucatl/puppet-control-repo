# OpenVox Server container migration

Decision: 18 October 2026. Run OpenVox 9 Server in the existing `infra` Compose
project in `docker/docker-compose.yml`, managed by `puppet-control-repo`.
Deploy first on `docker.home.arpa`, then move the same image and active state
to `atlas.home.arpa`. This makes the core infrastructure application stack
containerized; agents, Docker hosts and Proxmox remain host components.

This is the deployment target. The current server is still host-installed.
Image implementation does not complete the production rollout or PKI transition.
The tracked readiness, rollout and recovery work is in issues
[#12](https://github.com/Enucatl/puppet-control-repo/issues/12),
[#13](https://github.com/Enucatl/puppet-control-repo/issues/13) and
[#18](https://github.com/Enucatl/puppet-control-repo/issues/18).

## Image and package delivery

- Build an Ubuntu 26.04 amd64 image from the signed, versioned APT releases in
  `Enucatl/openvox-packages`. Those packages are built from the coordinated
  `feature/mldsa44` branches of the server, agent, SSL library and CA CLI;
  preserve the matching server/agent dependency inside the image.
- Publish the image as `ghcr.io/enucatl/openvox-server` after its selected
  package release passes acceptance. Use `<upstream-version>-mldsa.<build>`
  for unique image tags: `9.1.0-mldsa.13001` for the current package baseline.
  Move `:9.1.0` to the accepted image for that upstream version and `:latest`
  to the newest accepted image. The APT release remains `resolute-13001`;
  its codename-based identity is separate from OCI tags. Record the image tag,
  package release/versions, source revisions, runtime inputs, image-build
  revision and registry digest.
  Subsequent successful branch builds produce new images; deploy and recover
  by recorded digest rather than freezing development branches.
- Use Java 25 and explicitly apply the tested BCJSSE/security/signature settings
  in the foreground launcher. A container does not inherit systemd environment
  settings. Include the patched SSL library, CA CLI and Python dependencies for
  the real autosigner and ENC. Keep private identities and tokens out of images.
- Keep signed APT delivery for native fleet agents. A container's Ubuntu 26.04
  userland can run on the existing Docker host after container acceptance;
  native Ubuntu 24.04 and Debian/Proxmox agent compatibility remains a separate
  gate before the existing-host-first rollout.
- Verify GHCR visibility and pull authentication. Keep the deployed image,
  package archive/manifest, public archive key and any required registry access
  recoverable independently of Vault, the old Docker host and its caches.

## Runtime and integration

- Add the server to the existing infrastructure Compose file and reuse the
  existing deployment and hardening conventions. Run the installed foreground
  launcher as `puppet`, with explicit resource settings and graceful shutdown.
  The server needs neither privileged execution nor a Docker socket mount.
- Publish TCP 8140 directly as the baseline. OpenVox terminates TLS and validates
  client certificates. If Traefik routing is needed, use TCP TLS passthrough;
  ordinary HTTP TLS termination is outside this plan.
- Declare persistent mappings for `/etc/puppetlabs/puppetserver/ca`,
  `/etc/puppetlabs/puppet/ssl`, effective server/agent configuration and the
  writable state subdirectories below `/opt/puppetlabs/server/data/puppetserver`.
  Keep image-owned provider policy, vendored gems and service definitions from
  the selected image; a whole-directory volume must not mask them during an
  update. Preserve ownership and Docker user-namespace mappings. Container
  recreation must not initialize a new CA or replace an existing server identity.
- The image uses `server_data` below that data tree for its mutable vardir and
  `jruby-gems` for operator-installed gems. Map the old host's mutable vardir
  contents into `server_data` during rehearsal; retain the original checkpoint
  and do not copy its packaged gems or provider policy over image-owned files.
- Preserve code under `/etc/puppetlabs/code`, the bare Git repository, hooks,
  r10k configuration/cache and the production/dev environment model. Execute
  deployment/type generation where the container can read the resulting code;
  prove Git-triggered updates and environment-cache invalidation.
- Preserve the Python autosigner, ENC, exact certname/SAN checks, single-use
  Vault enrollment tokens, trust and Vault/Hiera module branch. Keep policy
  autosigning; do not replace it with blanket certificate acceptance.
- Version the bootstrap and restore commands so Docker/Compose, image access,
  configuration and protected state can be restored before managed convergence.
  Restoring Vault/OpenVox must not require a live instance of the same service
  or new enrollment against the unavailable old configuration server.

## Migration and recovery sequence

1. In #12, build/test the published image with disposable state and the real
   control-repo setup/enrollment scripts. Keep completed package/systemd tests
   as evidence for package delivery; separately prove container operation.
2. In #11, protect the old host-installed server and authority checkpoints plus
   the new container mappings, image and effective configuration. Rehearse
   restore and rollback with consistent authority, agent identity and trust.
3. In #13, stop the old host server and start the container with the preserved
   authority/state on `docker.home.arpa`. Prove existing-agent compatibility
   before the separately rehearsed ML-DSA authority/identity transition. Keep
   only one production server/CA writer and one listener active.
4. Complete the canary and fleet agent transition, then the existing FreeIPA
   gates. Keep the agreed rebuild order: Atlas, aperture, cognition.
5. In #18, independently restore the selected image and active ML-DSA state on
   isolated Atlas. Prove cold start without the old host's services; restore
   existing authorities rather than rerunning fresh initialization.
6. In #19, transfer the same containerized infrastructure stack and state to
   Atlas, activate its endpoint, and verify existing agents before provisioning
   dependent replacement guests. In #20, observe sustained operation and retire
   old services/identities only after the existing retention gates.

## Acceptance still required

- [ ] Publish a fork image with the selected package pair and source/runtime
      provenance; verify pull access and record the deployment digest.
- [ ] Prove foreground startup, readiness, resource use, graceful stop, restart,
      container recreation and host reboot without authority/identity changes.
- [ ] Prove real enrollment, catalog convergence, scheduled runs, facts/reports,
      ENC, Git/r10k deployment, Vault/Hiera, renewal and CRL/revocation behavior.
      Record actual TLS authentication signatures separately from key exchange.
- [ ] Prove isolated recovery with independently available image/configuration,
      restored volumes and old-host access blocked. Preserve the original
      authority and enrolled identities, ownership and least privilege.
- [ ] Prove image update and previous-digest rollback with compatible state;
      authority/identity rollback requires the matching protected checkpoints.
- [ ] Remove dependency on the old host-installed server after the cutover and
      rollback observation period. Keep host agents native and operational.

Image construction is implemented in `openvox-packages`; control-repo Compose
integration, live migration and these operational gates remain tracked work.
