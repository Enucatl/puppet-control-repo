# Docker Deployments and Image Refresh

This repository already refreshes Docker-based projects when code is pushed.
That covers image rebuilds tied to repository changes, but it does not keep
quiet services on newer upstream images between deploys.

The scheduled image refresh and the daily `docker system prune` job are
independent. Refreshes can pull newer upstream images before the old ones are
reclaimed, so disk space from unused layers may not drop until the next prune
run. The prune policy lives in `data/roles/docker.yaml`.

## Goal

Puppet manages scheduled refreshes for Docker Compose projects on
`docker.home.arpa` so containers periodically pick up upstream image updates
even when no code has changed.

The intended behavior is:

- keep the existing push-triggered deploy flow intact
- run a scheduled refresh for all present Docker Compose projects
- include projects with custom `build_command`
- wait for readiness on both push deployments and scheduled refreshes
- fail when readiness times out, a server exits before readiness, or setup fails
- surface failures through journald and the existing log pipeline

## Chosen Policy

- Refresh cadence: monthly, on the first Sunday at 04:00 local time
- Scope: all present projects in `profile::docker_host::git_deploy_projects`
- Custom build projects: included
- Failure notification: journald only
- Readiness gate: `docker compose up --force-recreate --wait --wait-timeout 300`

This is a broad policy on purpose. It favors image freshness and vulnerability
exposure reduction over minimizing container restarts.

## Implementation Shape

The existing Puppet profiles manage both deployment paths:

- `profile::docker_host` has a scheduled-refresh toggle and a default timer
  calendar
- `profile::docker_deploy` has a separate scheduled-refresh service and timer
- each opted-in project gets a `${name}-refresh.timer` that starts the
  `${name}-refresh.service`
- the push-triggered deploy service runs pull/build/up with native Compose waiting
- the scheduled refresh service uses the same flow, skips the commit-path gate,
  and uses the same readiness timeout
- both services retain `docker compose ps` for diagnostic output
- waiting applies even when scheduled refresh is disabled, including on `complex`

The default readiness timeout is 300 seconds. Override it per project through
the existing Hiera parameters:

```yaml
profile::docker_host::git_deploy_projects:
  myapp:
    wait_timeout: 600
    compose_file: docker-compose.prod.yml
    env_file: production.env
```

`wait_timeout` must be a positive integer. It controls Compose readiness waiting,
not the total pull/build/setup sequence. Compose waits for health checks where
configured; services without health checks receive only a running-state check.
`--wait` implies detached mode. File and environment flags apply to pull, up, and ps.

`deploy_command` remains an unchanged shell-command override. It must implement
its own waiting, timeout, and setup-job handling; `wait_timeout` does not modify
custom commands. For example, Beszel waits for its server before running setup:

```yaml
profile::docker_host::git_deploy_projects:
  beszel:
    build_command: "docker compose build alerts-init"
    deploy_command: "docker compose up -d --force-recreate --wait --wait-timeout 300 beszel && docker compose run --rm alerts-init"
```

The `&&` preserves readiness and setup failures in the systemd result. Backrest
and qBittorrent keep their existing `service_completed_successfully` dependencies:
Compose accepts these one-shot jobs only when they exit zero during native waiting
([Compose implementation](https://raw.githubusercontent.com/docker/compose/v5.5.1/pkg/compose/start.go)).
Profile-gated maintenance and setup jobs remain excluded from normal deployment;
do not enable their profiles unless the deployment explicitly needs them.

The Python checker and its post-start invocation are removed.
`profile::docker_deploy::health_check` temporarily remains as a cleanup class,
ensuring `/usr/local/sbin/docker-compose-health-check` is absent on existing hosts.

The plan intentionally avoids:

- Watchtower or another standalone container updater
- app-specific health checks
- email, webhook, or other new notification plumbing

## Verification

Verify changes before rollout with:

- Puppet parser validation for the touched manifests
- catalog tests for both services, custom commands, timeout overrides, and checker removal
- disposable Compose projects covering readiness, timeout, early exit, running-only
  services, successful/failed setup dependencies, and no selected services

Deploy through the normal Puppet process, then inspect:

- `systemctl list-timers '*-refresh.timer'`
- `systemctl cat <project>-refresh.timer`
- `systemctl cat <project>-deploy.service`
- `systemctl cat <project>-refresh.service`
- removal of `/usr/local/sbin/docker-compose-health-check`

Observe the next deployment or refresh through its systemd result and
`journalctl -u <project>-deploy.service` or `journalctl -u <project>-refresh.service`.
Do not restart production projects solely for verification. No automatic rollback
or new monitoring service is added.

## Assumptions

- "Monthly Sunday 04:00" means the first Sunday of each month at 04:00 local
  time.
- Logging through journald is sufficient for operational visibility because the
  host already ships logs onward.
- Projects can opt out later if any specific stack proves too sensitive for
  scheduled refreshes.
