# Installs the Beszel agent binary and manages its systemd unit.
#
# Runs as the FreeIPA `beszel` user (created via freeipa_users on docker,
# available on all hosts through SSSD). Do not use get.beszel.dev — it would
# create a conflicting local user. Pin $version to the hub image tag.
#
# Required Hiera (Vault-backed secrets; hub_url defaults in common.yaml):
#   profile::beszel_agent::key
#   profile::beszel_agent::token
#
# SYSTEM_NAME defaults to $trusted['certname'] (do not hardcode per node).
# On the Docker host, add beszel to the local docker group via
# freeipa_users::user_groups so the agent can read docker.sock.
#
class profile::beszel_agent (
  String[1]                $hub_url,
  Sensitive[String[1]]     $key,
  Sensitive[String[1]]     $token,
  String[1]                $version               = '0.19.0',
  String[1]                $user                  = 'beszel',
  String[1]                $group                 = $user,
  String[1]                $system_name           = $trusted['certname'],
  String[1]                $listen                = '45876',
  Stdlib::Absolutepath     $binary_path           = '/usr/local/bin/beszel-agent',
  Stdlib::Absolutepath     $config_dir            = '/etc/beszel-agent',
  Stdlib::Absolutepath     $data_dir              = '/var/lib/beszel-agent',
  Stdlib::Absolutepath     $cache_dir             = '/var/cache/beszel-agent',
  Boolean                  $enable_docker_metrics = false,
  Enum['present','absent'] $ensure                = 'present',
) {
  $arch = $facts['os']['architecture'] ? {
    'amd64'   => 'amd64',
    'x86_64'  => 'amd64',
    'aarch64' => 'arm64',
    'arm64'   => 'arm64',
    default   => fail("Unsupported architecture for beszel-agent: ${facts['os']['architecture']}"),
  }
  $kernel       = downcase($facts['kernel'])
  $archive_name = "beszel-agent_${kernel}_${arch}.tar.gz"
  $source       = "https://github.com/henrygd/beszel/releases/download/v${version}/${archive_name}"
  $key_file     = "${config_dir}/key"
  $token_file   = "${config_dir}/token"
  $marker       = "${cache_dir}/.installed-${version}"

  # IPA user is created on the docker node; other hosts resolve it via SSSD.
  # Collectors avoid parse-order issues with defined().
  Exec <| title == "ipa-user-add-${user}" |> -> File[$config_dir]
  Exec <| title == "ipa-user-add-${user}" |> -> File[$data_dir]
  Exec <| title == "add_ipa_user_${user}_to_groups" |> -> Systemd::Unit_file['beszel-agent.service']

  if $ensure == 'present' {
    file { [$config_dir, $cache_dir]:
      ensure => directory,
      owner  => 'root',
      group  => $group,
      mode   => '0750',
    }

    file { $data_dir:
      ensure => directory,
      owner  => $user,
      group  => $group,
      mode   => '0750',
    }

    archive { "${cache_dir}/${archive_name}":
      ensure          => present,
      source          => $source,
      extract         => true,
      extract_path    => $cache_dir,
      extract_command => "tar -xzf %s -C ${cache_dir} beszel-agent",
      creates         => $marker,
      cleanup         => false,
      require         => File[$cache_dir],
    }
    -> exec { 'install-beszel-agent-binary':
      command => "install -o root -g root -m 0755 ${cache_dir}/beszel-agent ${binary_path} && touch ${marker}",
      creates => $marker,
      path    => ['/usr/bin', '/bin'],
      notify  => Service['beszel-agent.service'],
    }

    file { $key_file:
      ensure    => file,
      owner     => 'root',
      group     => $group,
      mode      => '0640',
      content   => Sensitive("${key.unwrap}\n"),
      show_diff => false,
      notify    => Service['beszel-agent.service'],
    }

    file { $token_file:
      ensure    => file,
      owner     => 'root',
      group     => $group,
      mode      => '0640',
      content   => Sensitive("${token.unwrap}\n"),
      show_diff => false,
      notify    => Service['beszel-agent.service'],
    }

    $docker_env = $enable_docker_metrics ? {
      true    => "Environment=DOCKER_HOST=unix:///var/run/docker.sock\n",
      default => '',
    }

    systemd::unit_file { 'beszel-agent.service':
      ensure  => present,
      content => @("UNIT"),
        [Unit]
        Description=Beszel Agent
        Documentation=https://beszel.dev/guide/agent-installation
        After=network-online.target
        Wants=network-online.target

        [Service]
        Type=simple
        User=${user}
        Group=${group}
        ExecStart=${binary_path}
        Environment=LISTEN=${listen}
        Environment=HUB_URL=${hub_url}
        Environment=SYSTEM_NAME=${system_name}
        Environment=KEY_FILE=${key_file}
        Environment=TOKEN_FILE=${token_file}
        Environment=DATA_DIR=${data_dir}
        ${docker_env}Restart=on-failure
        RestartSec=5
        NoNewPrivileges=yes
        ProtectHome=read-only
        ProtectSystem=strict
        ProtectKernelTunables=yes
        ProtectKernelModules=yes
        ProtectControlGroups=yes
        PrivateTmp=yes
        ReadWritePaths=${data_dir}
        ReadOnlyPaths=${config_dir}

        [Install]
        WantedBy=multi-user.target
        | UNIT
      enable  => true,
      active  => true,
      require => [
        Exec['install-beszel-agent-binary'],
        File[$key_file],
        File[$token_file],
        File[$data_dir],
      ],
    }
  } else {
    systemd::unit_file { 'beszel-agent.service':
      ensure => absent,
    }

    file { [$key_file, $token_file, $binary_path]:
      ensure => absent,
    }
  }
}
