# Atlas service configuration

Each service is maintained in its own repository under `/opt/docker/<project>`.
Compose files and application configuration are versioned there. There are no
generated `atlas-compose.json` files or migration configuration renderers.

Keep shared settings in `/opt/docker/.env`, project settings in
`/opt/docker/<project>/.env`, and secret files in the project's `secrets/`
directory. Never commit environment files, secret files, resolved Compose output,
or configuration containing expanded credentials. Project values override shared
values; do not copy shared assignments into each project.

Systemd deployment and automation units select these two environment files using
`COMPOSE_ENV_FILES`. For the same behavior in an operator shell:

```bash
cd /opt/docker/<project>
COMPOSE_ENV_FILES=/opt/docker/.env,.env docker compose config --quiet
COMPOSE_ENV_FILES=/opt/docker/.env,.env docker compose up -d --wait
```

Compose does not automatically load the parent `.env`. Use the explicit
selection above for every command, or export `COMPOSE_ENV_FILES` in the current
shell. Avoid printing resolved configuration: it can contain credentials.

The control repository's root Compose project includes OpenVox, Vault, its
unsealer, the APT cache, the Wolf controller, GoFlow2 and FreeIPA. The stack owns
its `infra_traefik` ingress network, which Traefik consumes as an external
network. Existing authority and Wolf state volumes retain their external names.
Bootstrap and authority recovery use their explicit inputs;
follow [Atlas recovery](atlas-recovery.md).
Cerebrum's Wolf/vLLM projects have no automatic Git deployments. Their existing
on-demand controllers select when to start them. See the [Cerebrum handover](archive/migration/cerebrum-cutover.md)
for controller, routing and NFS transitions.

The completed migration's renderers and frozen Compose outputs are retained only
in Git history and protected operator evidence. Existing database volumes and
rollback VM state remain separate from service configuration.
