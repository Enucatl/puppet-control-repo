# Opt-in publisher: this job cannot start or recreate any other Compose service.
class profile::openvox_crl_refresh (
  Hash                $config,
  Stdlib::Absolutepath $project_dir = '/opt/docker/puppet-control-repo',
  Integer[0]          $credentials_uid = 100999,
  Integer[0]          $credentials_gid = 100999,
  Stdlib::Absolutepath $client_cert = "/etc/puppetlabs/puppet/ssl/certs/${trusted['certname']}.pem",
  Stdlib::Absolutepath $client_key = "/etc/puppetlabs/puppet/ssl/private_keys/${trusted['certname']}.pem",
) {
  include profile::openvox_crl

  file { '/etc/openvox-crl/publisher.json':
    ensure  => file,
    owner   => 'root',
    group   => 'root',
    mode    => '0644',
    content => to_json($config),
    require => File['/etc/openvox-crl'],
  }

  # Defaults match Docker's userns-remap base 100000 + container uid/gid 999.
  file { '/etc/openvox-crl/credentials':
    ensure  => directory,
    owner   => $credentials_uid,
    group   => $credentials_gid,
    mode    => '0700',
    require => File['/etc/openvox-crl'],
  }

  systemd::unit_file { 'openvox-crl-refresh.service':
    content => @("UNIT"),
      [Unit]
      Description=Validate and publish the OpenVox and Vault CRL chains
      Requires=docker.service
      Wants=network-online.target
      After=docker.service network-online.target
      OnFailure=openvox-crl-alert@openvox-crl-refresh.service

      [Service]
      Type=oneshot
      User=root
      WorkingDirectory=${project_dir}
      Environment=COMPOSE_ENV_FILES=../.env,./.env
      ExecStartPre=/usr/bin/install -o ${credentials_uid} -g ${credentials_gid} -m 0600 "${client_cert}" /etc/openvox-crl/credentials/client.crt
      ExecStartPre=/usr/bin/install -o ${credentials_uid} -g ${credentials_gid} -m 0600 "${client_key}" /etc/openvox-crl/credentials/client.key
      ExecStart=/usr/bin/docker compose -f docker/docker-compose.yml run --rm --no-deps --pull never openvox-crl-refresh
      TimeoutStartSec=180
      StandardOutput=journal
      StandardError=journal
      | UNIT
    require => [File['/etc/openvox-crl/publisher.json'], File['/etc/openvox-crl/credentials']],
  }

  systemd::unit_file { 'openvox-crl-refresh.timer':
    content => @(UNIT),
      [Unit]
      Description=Refresh published CRLs every five minutes

      [Timer]
      OnBootSec=1min
      OnUnitActiveSec=5min
      Unit=openvox-crl-refresh.service

      [Install]
      WantedBy=timers.target
      | UNIT
    enable  => true,
    active  => true,
    require => Systemd::Unit_file['openvox-crl-refresh.service'],
  }
}
