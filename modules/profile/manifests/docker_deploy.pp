# Manages a systemd path+service pair that redeploys a docker compose project
# whenever its git repo receives a push (i.e. refs/heads/<branch> is modified).
#
# The resource title must match the project folder name under /opt/docker/.
#
# Parameters:
#   branch        - Git branch to watch (default: 'main')
#   run_as        - User that runs docker compose (default: 'user')
#   pull          - Pull updated images before starting (default: false)
#   build_command - Custom build command run after pull and before up
#                   (wrapped in bash -c '...'); omit for image-only projects
#   compose_file  - Path to the compose file, relative to the project dir or
#                   absolute; omit to let docker compose auto-discover
#   env_file      - Path to an env file passed via --env-file; omit to use
#                   docker compose's default (.env in project dir)
#
# Example (Hiera, via profile::docker_host::git_deploy_projects):
#
#   profile::docker_host::git_deploy_projects:
#     crabberbot:
#       build_command: "CARGO_PACKAGE_VERSION=$(git describe --long | sed 's/-/./') docker compose build"
#     paperless-ngx:
#       pull: true
#     myapp:
#       pull: true
#       env_file: /opt/docker/myapp/production.env
#       compose_file: docker-compose.prod.yml
#
define profile::docker_deploy (
  String           $branch        = 'main',
  String           $run_as        = 'user',
  Boolean          $pull          = true,
  Optional[String] $build_command = undef,
  Optional[String] $compose_file  = undef,
  Optional[String] $env_file      = undef,
) {
  $base_dir = "/opt/docker/${name}"

  # Build the global docker compose flags (--env-file and -f apply to all subcommands)
  $envfile_flag = $env_file    ? { undef => '',   default => " --env-file ${env_file}" }
  $file_flag    = $compose_file ? { undef => '',   default => " -f ${compose_file}" }
  $compose_cmd  = "/usr/bin/docker compose${envfile_flag}${file_flag}"

  # Assemble ExecStart lines in order: pull → custom build → up
  $pull_exec  = $pull          ? { true  => ["ExecStart=${compose_cmd} pull"], default => [] }
  $build_exec = $build_command ? { undef => [],                                default => ["ExecStart=/bin/bash -c '${build_command}'"] }
  $exec_start_str = join($pull_exec + $build_exec + ["ExecStart=${compose_cmd} up -d"], "\n")

  systemd::unit_file { "${name}-deploy.path":
    content => @("UNIT")
      [Unit]
      Description=Watch for ${name} git pushes

      [Path]
      PathModified=${base_dir}/.git/refs/heads/${branch}

      [Install]
      WantedBy=multi-user.target
      | UNIT
    enable  => true,
    active  => true,
  }

  systemd::unit_file { "${name}-deploy.service":
    content => @("UNIT")
      [Unit]
      Description=Rebuild and restart ${name}
      Requires=docker.service
      After=docker.service
      After=network-online.target

      [Service]
      Type=oneshot
      User=${run_as}
      WorkingDirectory=${base_dir}
      ExecStartPre=/bin/sleep 5
      ${exec_start_str}
      StandardOutput=journal
      StandardError=journal
      SyslogIdentifier=${name}-deploy
      | UNIT
    enable  => false,
    active  => false,
  }
}
