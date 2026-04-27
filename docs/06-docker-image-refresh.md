# Docker Image Refresh Plan

This repository already refreshes Docker-based projects when code is pushed.
That covers image rebuilds tied to repository changes, but it does not keep
quiet services on newer upstream images between deploys.

## Goal

Add a Puppet-managed scheduled refresh for Docker Compose projects on
`docker.home.arpa` so containers periodically pick up upstream image updates
even when no code has changed.

The intended behavior is:

- keep the existing push-triggered deploy flow intact
- run a scheduled refresh for all present Docker Compose projects
- include projects with custom `build_command`
- fail the refresh if Docker Compose reports an unhealthy or exited stack
- surface failures through journald and the existing log pipeline

## Chosen Policy

- Refresh cadence: monthly, on the first Sunday at 04:00 local time
- Scope: all present projects in `profile::docker_host::git_deploy_projects`
- Custom build projects: included
- Failure notification: journald only
- Health gate: structured `docker compose ps --all --format json` after deploy

This is a broad policy on purpose. It favors image freshness and vulnerability
exposure reduction over minimizing container restarts.

## Implementation Shape

The implementation should extend the existing Puppet profiles rather than add a
parallel updater:

- `profile::docker_host` gets a scheduled-refresh toggle and a default timer
  calendar
- `profile::docker_deploy` gets a separate scheduled-refresh service and timer
- each opted-in project gets a `${name}-refresh.timer` that starts the
  `${name}-refresh.service`
- the push-triggered deploy service keeps the current pull/build/up flow
- the scheduled refresh service uses the same flow, skips the commit-path gate,
  and checks stack status with `docker compose ps --all --format json`
- exited one-shot containers pass only when their exit code is zero

The plan intentionally avoids:

- Watchtower or another standalone container updater
- app-specific health checks
- email, webhook, or other new notification plumbing

## Verification

After implementation, verify the behavior with:

- Puppet parser validation for the touched manifests
- Puppet noop or compile on the Docker node
- `systemctl list-timers '*-refresh.timer'`
- `systemctl cat <project>-refresh.timer`
- `systemctl cat <project>-deploy.service`
- one manual refresh of a low-risk project to confirm the service logs and
  failure behavior

## Assumptions

- "Monthly Sunday 04:00" means the first Sunday of each month at 04:00 local
  time.
- Logging through journald is sufficient for operational visibility because the
  host already ships logs onward.
- Projects can opt out later if any specific stack proves too sensitive for
  scheduled refreshes.
