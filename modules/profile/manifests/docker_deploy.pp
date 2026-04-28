# Manages a systemd path+service pair that redeploys a docker compose project
# whenever a push to the remote is detected (i.e. refs/remotes/origin/<branch> is modified).
#
# The resource title must match the project folder name under /opt/docker/.
#
# Parameters:
#   ensure        - 'present' or 'absent' (default: 'present')
#   branch        - Git branch to watch (default: 'main')
#   run_as        - User that runs docker compose (default: 'user')
#   pull          - Pull updated images before starting (default: true)
#   build_command - Custom build command run after pull and before deploy
#                   (wrapped in bash -c '...'); omit for image-only projects
#   deploy_command - Custom deploy command run after pull/build
#                    (wrapped in bash -c '...'); omit to use the default
#                    docker compose up -d --force-recreate
#   compose_file  - Path to the compose file, relative to the project dir or
#                   absolute; omit to let docker compose auto-discover
#   env_file      - Path to an env file passed via --env-file; omit to use
#                   docker compose's default (.env in project dir)
#   watch_dir     - Only redeploy if the last push touched files under this
#                   subdirectory (relative to the repo root); omit to redeploy
#                   on any push
#   scheduled_refresh - Create a periodic refresh timer that starts the deploy
#                       service
#   refresh_calendar  - systemd OnCalendar value for scheduled_refresh
#
# Example (Hiera, via profile::docker_host::git_deploy_projects):
#
#   profile::docker_host::git_deploy_projects:
#     crabberbot:
#       build_command: "CARGO_PACKAGE_VERSION=$(git describe --long | sed 's/-/./') docker compose build"
#     paperless-ai:
#       deploy_command: "docker compose --profile ai up -d"
#     paperless-ngx:
#       pull: true
#     myapp:
#       pull: true
#       env_file: /opt/docker/myapp/production.env
#       compose_file: docker-compose.prod.yml
#
define profile::docker_deploy (
  Enum['present','absent'] $ensure        = 'present',
  String                   $branch        = 'main',
  String                   $run_as        = 'user',
  Boolean                  $pull          = true,
  Optional[String]         $build_command = undef,
  Optional[String]         $deploy_command = undef,
  Optional[String]         $compose_file  = undef,
  Optional[String]         $env_file      = undef,
  Optional[String]         $watch_dir     = undef,
  Boolean                  $scheduled_refresh = false,
  String                   $refresh_calendar  = 'Sun *-*-01..07 04:00:00',
) {
  include profile::docker_deploy::health_check

  $base_dir = "/opt/docker/${name}"

  # Build the global docker compose flags (--env-file and -f apply to all subcommands)
  $envfile_flag = $env_file    ? { undef => '',   default => " --env-file ${env_file}" }
  $file_flag    = $compose_file ? { undef => '',   default => " -f ${compose_file}" }
  $compose_cmd  = "/usr/bin/docker compose${envfile_flag}${file_flag}"

  # Assemble ExecStart lines in order: pull -> custom build -> deploy
  $pull_exec       = $pull          ? { true  => ["ExecStart=${compose_cmd} pull"], default => [] }
  $build_exec      = $build_command ? { undef => [],                                default => ["ExecStart=/bin/bash -c '${build_command}'"] }
  $deploy_exec     = $deploy_command ? {
    undef   => ["ExecStart=${compose_cmd} up -d --force-recreate"],
    default => ["ExecStart=/bin/bash -c '${deploy_command}'"],
  }
  $exec_start_str  = join($pull_exec + $build_exec + $deploy_exec, "\n")
  $watch_dir_str   = $watch_dir ? {
    undef   => '',
    default => "ExecCondition=/bin/bash -c 'git diff --name-only HEAD@{1} HEAD -- ${watch_dir} | grep -q .'\n",
  }
  $refresh_health_check_str = $scheduled_refresh ? {
    true    => "ExecStartPost=/usr/local/sbin/docker-compose-health-check ${compose_cmd}\n",
    default => '',
  }

  systemd::unit_file { "${name}-deploy.path":
    ensure  => $ensure,
    content => @("UNIT"),
      [Unit]
      Description=Watch for ${name} remote git pushes

      [Path]
      PathModified=${base_dir}/.git/refs/remotes/origin/${branch}

      [Install]
      WantedBy=multi-user.target
      | UNIT
    enable  => $ensure == 'present',
    active  => $ensure == 'present',
  }

  systemd::unit_file { "${name}-deploy.service":
    ensure  => $ensure,
    content => @("UNIT"),
      [Unit]
      Description=Rebuild and restart ${name}
      Requires=docker.service
      After=docker.service
      After=network-online.target

      [Service]
      Type=oneshot
      User=${run_as}
      WorkingDirectory=${base_dir}
      Environment=COMPOSE_ENV_FILES=../.env,./.env
      ${watch_dir_str}ExecStartPre=/bin/sleep 5
      ${exec_start_str}
      ExecStartPost=${compose_cmd} ps
      ${refresh_health_check_str}
      StandardOutput=journal
      StandardError=journal
      SyslogIdentifier=${name}-deploy
      | UNIT
    enable  => false,
    active  => false,
    require => Class['profile::docker_deploy::health_check'],
  }

  if $scheduled_refresh {
    systemd::unit_file { "${name}-refresh.service":
      ensure  => $ensure,
      content => @("UNIT"),
        [Unit]
        Description=Refresh and restart ${name}
        Requires=docker.service
        After=docker.service
        After=network-online.target

        [Service]
        Type=oneshot
        User=${run_as}
        WorkingDirectory=${base_dir}
        Environment=COMPOSE_ENV_FILES=../.env,./.env
        ExecStartPre=/bin/sleep 5
        ${exec_start_str}
        ExecStartPost=${compose_cmd} ps
        ${refresh_health_check_str}
        StandardOutput=journal
        StandardError=journal
        SyslogIdentifier=${name}-refresh
        | UNIT
      enable  => false,
      active  => false,
      require => Class['profile::docker_deploy::health_check'],
    }

    systemd::unit_file { "${name}-refresh.timer":
      ensure  => $ensure,
      content => @("UNIT"),
        [Unit]
        Description=Refresh ${name} on a monthly schedule

        [Timer]
        OnCalendar=${refresh_calendar}
        Persistent=true
        Unit=${name}-refresh.service

        [Install]
        WantedBy=timers.target
        | UNIT
      enable  => $ensure == 'present',
      active  => $ensure == 'present',
      require => Systemd::Unit_file["${name}-refresh.service"],
    }
  }
}
