# Development Infrastructure And Cryptography Migration Plan

The first goal is to build a faithful development mirror of the infrastructure
bootstrap path. Modern cryptography experiments should run only after that dev
mirror can reproduce the current production setup end to end.

Ed25519 and ECDSA are modern classical algorithms, not post-quantum algorithms.
They reduce RSA dependence and operational cost, but they are still vulnerable
to a cryptographically relevant quantum computer. Keep long-lived classical
trust anchors short-lived where practical.

## Summary

- Build `docker.dev.home.arpa` as a full development infrastructure VM.
- Keep service names unchanged and switch only `DOMAIN` from `home.arpa` to
  `dev.home.arpa`.
- Use the supported Puppet/r10k branch model:
  - `production` branch -> `production` Puppet environment.
  - `dev` branch -> `dev` Puppet environment.
- Mirror the production bootstrap path in development: Vault, FreeIPA, Puppet
  Server, Puppet external CA import, r10k, autosign, ENC, Puppet agent
  enrollment, and Vault cert auth.
- Run Ed25519/ECDSA experiments only after the dev mirror proves the existing
  deployment path.

## Current State

- Puppet Server is host-installed on `docker.home.arpa`, not defined in
  `docker/docker-compose.yml`.
- `docker/docker-compose.yml` currently runs Vault, FreeIPA, apt-cacher-ng, and
  supporting services.
- `docker/puppet/config/r10k.yaml` deploys Puppet environments from branches
  under `/etc/puppetlabs/code/environments`.
- `post-receive` watches `production` and `dev` branches
  and deploys the corresponding Puppet environment.
- `scripts/external_node_classifier.py` maps `*.dev.home.arpa` certnames to the
  `dev` Puppet environment.
- `scripts/autosign.py` allows both `<host>.home.arpa` and
  `<host>.dev.home.arpa`.
- Docker application checkouts are expected under `/opt/docker/<project>`.
- `profile::docker_deploy` runs Compose from `/opt/docker/<project>` and sets
  `COMPOSE_ENV_FILES=../.env,./.env`.
- The infrastructure stack checkout is `/opt/docker/puppet-control-repo`; its
  Compose file is `docker/docker-compose.yml`, relative to that checkout.
- Vault bootstrap creates a 10-year root CA, a 5-year intermediate CA, and a
  long-lived Vault TLS certificate.
- Puppet external CA provisioning currently creates a Puppet CA key with
  `openssl genrsa 4096`.
- FreeIPA uses an external-CA flow where FreeIPA generates a CSR and Vault signs
  it as an intermediate CA.

## Phase 1: Dev Infrastructure Mirror

Provision `docker.dev.home.arpa` as the development equivalent of
`docker.home.arpa`.

- Use the same checkout paths as production:
  - Working checkout: `/opt/docker/puppet-control-repo`
  - Bare r10k source repo: `/opt/git/puppet-control-repo.git`
  - Puppet environments: `/etc/puppetlabs/code/environments`
  - r10k config: `/etc/puppetlabs/r10k/r10k.yaml`
  - r10k cache: `/var/cache/r10k`
- Provision the VM with the same administrative login path used by the current
  Proxmox Docker VM script:
  - Cloud-init user: `user_l`
  - SSH key: `~/.ssh/id_ed25519.pub` from the Proxmox host invoking
    `proxmox/docker-server.sh`
  - QEMU guest agent enabled so provisioning can read cloud-init logs and run
    post-boot checks.
  - Passwordless sudo for `user_l`, matching the existing cloud-init template
    expectations for installation and verification.
- Keep the same Compose working directory and command shape:
  - Working directory: `/opt/docker/puppet-control-repo`
  - Compose file: `docker/docker-compose.yml`
  - Build command: `docker compose -f docker/docker-compose.yml build`
  - Deploy command: `docker compose -f docker/docker-compose.yml up -d --force-recreate`
  - Compose environment files: `COMPOSE_ENV_FILES=../.env,./.env`
- Install Puppet Server on the dev VM with the same host-installed Puppet Server
  model used in production.
- Run the same repo bootstrap path on the dev VM:
  - `05-clone-puppet-repo.sh` creates `/opt/git/puppet-control-repo.git`,
    installs `r10k`, installs the post-receive hook, and creates Puppet
    environment directories.
  - `01-pki-core-setup.sh` initializes dev Vault PKI.
  - `02-pki-intermediate.sh` initializes the dev intermediate PKI.
  - `03-puppet-external-ca.sh` creates and imports the dev Puppet CA.
  - `04-sign-csr.sh` signs the dev FreeIPA external-CA CSR.
  - `06-vault-puppet.sh` and `08-vault-puppet-policy.sh` configure dev Vault
    cert auth and Puppet policy access.
- Run dev scripts with `DOMAIN=dev.home.arpa`.
- Create the same environment-file layout as production:
  - `/opt/docker/.env` for host-wide Docker stack defaults shared by all
    projects on the VM.
  - `/opt/docker/puppet-control-repo/.env` for this repo's infrastructure stack
    secrets and overrides.
  - `/opt/docker/puppet-control-repo/docker/.env` only if a script must be run
    from `docker/` and needs local overrides; otherwise prefer the two standard
    files above.
- Put development values in the dev VM env files:
  - `DOMAIN=dev.home.arpa`
  - `DOCKER_DOMAIN=docker.dev.home.arpa`
  - `VAULT_ADDR=https://hcv.dev.home.arpa:8200` after TLS bootstrap
  - `VAULT_CACERT=/etc/ssl/certs/ca-certificates.crt` after trust is installed
  - `KEYS_FILE=/certificates/keys.json` for the Vault setup containers
  - `PASSWORD=<dev FreeIPA admin password>`
- During first Vault bootstrap, use the insecure Vault address only inside the
  dev setup boundary, then switch the dev `.env` to
  `VAULT_ADDR=https://hcv.dev.home.arpa:8200` once `vault.crt` and `vault.key`
  exist.
- Keep service names unchanged:
  - `docker.${DOMAIN}`
  - `hcv.${DOMAIN}`
  - `freeipa.${DOMAIN}`
  - `vault.${DOMAIN}`
  - `wolf.docker.${DOMAIN}`
- Keep dev Vault data, FreeIPA data, Puppet CA state, certificates, and Docker
  volumes local to the dev VM.
- Do not point production agents at the dev Puppet Server.

## Phase 2: Branch And Domain Parameterization

Make the dev mirror use the same code paths as production while changing only
the domain and branch.

- Introduce one canonical domain value in Puppet data, defaulting to
  `home.arpa` on `production` and `dev.home.arpa` on `dev`.
- Replace hard-coded `home.arpa` values in Puppet data and scripts with the
  domain value where those values represent environment-local service names.
- Keep globally intentional production references explicit if a dev service
  must still call production.
- Make `docker/vault/scripts/config.sh` honor environment overrides for:
  - `DOMAIN`
  - `VAULT_FQDN`
  - `FREEIPA_FQDN`
  - `DOCKER_FQDN`
  - `FREEIPA_CONTAINER`
- Convert Compose health checks, hostnames, Traefik labels, Vault URLs, and
  FreeIPA install options to `${DOMAIN}`-derived values where they describe the
  local stack.
- Use `DOMAIN=home.arpa` in production `.env` and `DOMAIN=dev.home.arpa` in dev
  `.env`.
- Preserve `profile::docker_host::git_deploy_projects` semantics for the dev
  node:
  - Project title remains `puppet-control-repo`.
  - Base directory remains `/opt/docker/puppet-control-repo`.
  - `compose_file` remains `docker/docker-compose.yml`.
  - `watch_dir` remains `docker`.
  - `branch` is `dev` on `docker.dev.home.arpa` and `production` on
    `docker.home.arpa`.
- Keep `COMPOSE_ENV_FILES=../.env,./.env` as the standard deploy environment
  rather than introducing per-environment service names or alternate project
  directories.
- Keep the existing branch deployment model:

  ```sh
  sudo -u puppet /opt/puppetlabs/puppet/bin/r10k deploy environment dev --modules -v info
  sudo -u puppet /opt/puppetlabs/puppet/bin/puppet generate types --environment dev
  ```

## Phase 3: Baseline Dev Verification

Before changing cryptography, prove the dev mirror can reproduce production
behavior.

- Verify the dev Puppet Server runs via systemd.
- Verify SSH access works as `user_l@docker.dev.home.arpa` with the injected
  Ed25519 key.
- Verify `user_l` can run `sudo -n true` without an interactive password
  prompt.
- Verify `/opt/docker/puppet-control-repo` exists and is checked out on the
  `dev` branch.
- Verify `/opt/docker/.env` and `/opt/docker/puppet-control-repo/.env` exist and
  contain dev-domain values, not production-domain values.
- Verify the generated systemd deploy unit for `puppet-control-repo` uses:
  - `WorkingDirectory=/opt/docker/puppet-control-repo`
  - `Environment=COMPOSE_ENV_FILES=../.env,./.env`
  - `docker compose -f docker/docker-compose.yml`
- Verify r10k deploys both `production` and `dev` environments on the dev VM.
- Verify the post-receive hook deploys the pushed branch.
- Enroll a disposable dev Puppet agent against the dev Puppet Server.
- Confirm the ENC selects `dev` for `*.dev.home.arpa`.
- Confirm autosign accepts a CSR with a valid Vault token.
- Confirm a full Puppet agent run compiles from the `dev` branch.
- Confirm dev Vault cert auth accepts dev Puppet certificates.
- Confirm dev FreeIPA completes external-CA installation and serves
  `freeipa.dev.home.arpa`.
- Confirm production Vault, FreeIPA, Puppet CA, Puppet Server, and agents are
  untouched.

## Phase 4: Cryptography Experiments

After the dev mirror is proven, use it to test modern cryptography changes.

### Vault PKI

- Add Ed25519 PKI mounts or issuers in dev first; do not replace production
  mounts in place.
- Generate dev root and intermediate CAs with `key_type=ed25519`.
- Add an Ed25519 service-cert role for compatible services.
- Keep an ECDSA P-256 or RSA compatibility role for Java, NSS, browsers,
  printers, appliances, or other clients that reject Ed25519 X.509.
- Reduce new root, intermediate, and Vault server certificate lifetimes from
  the current multi-year values.

### Puppet

- Do not migrate Puppet directly to Ed25519.
- Puppet documentation lists private key `key_type` values as `rsa` and `ec`,
  not Ed25519.
- Keep RSA-4096 as the production-safe Puppet CA baseline until dev proves a
  better path.
- Test an EC Puppet CA only on the dev Puppet Server:
  - Generate an EC Puppet CA key.
  - Sign/import it through the dev Vault-backed external CA path.
  - Validate Puppet Server restart, test cert generation, issuer encoding,
    agent bootstrap, catalog retrieval, CRL handling, and dev Vault cert auth.
- Promote only after every managed dev Puppet agent completes clean bootstrap
  and catalog runs.

### FreeIPA

- Keep the current external-CA CSR flow as the baseline.
- Test Ed25519 parent/signature material only in dev.
- Inspect `/data/ipa.csr` and validate FreeIPA/Dogtag behavior before making
  assumptions.
- If Ed25519 fails, test ECDSA as the modern fallback.
- If ECDSA also fails, keep FreeIPA RSA-backed and move only compatible
  service TLS certificates to Vault Ed25519.

## Verification Commands

- Inspect certificate algorithms:

  ```sh
  openssl x509 -in cert.pem -noout -text
  ```

- Verify Puppet Server state:

  ```sh
  ssh user_l@docker.dev.home.arpa
  sudo -n true
  systemctl status puppetserver --no-pager
  /opt/puppetlabs/bin/puppetserver ca list --all
  ```

- Verify r10k deployment:

  ```sh
  sudo -u puppet /opt/puppetlabs/puppet/bin/r10k deploy environment dev --modules -v info
  sudo -u puppet /opt/puppetlabs/puppet/bin/puppet generate types --environment dev
  ```

- Verify a dev agent:

  ```sh
  /opt/puppetlabs/bin/puppet config set server docker.dev.home.arpa --section main
  /opt/puppetlabs/bin/puppet ssl bootstrap
  /opt/puppetlabs/bin/puppet agent --test --environment dev
  ```

- Verify Vault cert auth with a dev Puppet certificate:

  ```sh
  VAULT_ADDR=https://hcv.dev.home.arpa:8200 \
  VAULT_CLIENT_CERT=/etc/puppetlabs/puppet/ssl/certs/<certname>.pem \
  VAULT_CLIENT_KEY=/etc/puppetlabs/puppet/ssl/private_keys/<certname>.pem \
  vault login -method=cert -no-store name=puppet
  ```

## Assumptions

- `dev.home.arpa` is the canonical development domain.
- Service names are stable across environments; `DOMAIN` is the environment
  switch.
- A single production Puppet Server with multiple r10k environments is enough
  for ordinary catalog testing.
- A separate dev Puppet Server is required for testing Puppet CA algorithms,
  Puppet Server bootstrap, and deployment scripts safely.
- Ed25519 is preferred for Vault-managed X.509 where consumers accept it.
- ECDSA is the preferred compatibility fallback before RSA.
- Modern classical cryptography changes must remain branch-local until the dev
  mirror proves compatibility.
