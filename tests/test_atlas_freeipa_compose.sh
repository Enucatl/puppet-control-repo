#!/usr/bin/env bash
set -euo pipefail

repo=$(cd "$(dirname "$0")/.." && pwd)
docker compose --env-file /dev/null -f "$repo/docker/freeipa/atlas-compose.yaml" config --format json |
  ruby -rjson -e '
    config = JSON.parse(STDIN.read)
    ipa = config.fetch("services").fetch("ipa")
    abort "IPA must use the published ML-DSA image directly" unless ipa.fetch("image").match?(/\Aghcr\.io\/enucatl\/freeipa-server@sha256:[0-9a-f]{64}\z/)
    abort "IPA must use native startup" if ipa["build"] || ipa["entrypoint"] != ["/usr/local/sbin/init"]
    mounts = ipa.fetch("volumes")
    abort "IPA must keep its native data volume" unless mounts[0].fetch("type") == "volume" && mounts[0].fetch("source") == "ipa_data" && mounts[0].fetch("target") == "/data"
    bootstrap = mounts.find { |mount| mount["target"] == "/bootstrap" }
    passwords = mounts.find { |mount| mount["target"] == "/data/ipa-server-install-options" }
    abort "IPA must read adjacent secrets" unless mounts.length == 3 && bootstrap && bootstrap["source"].end_with?("/docker/freeipa/secrets") && passwords && passwords["source"] == bootstrap["source"] + "/ipa-server-install-options" && [bootstrap, passwords].all? { |mount| mount["type"] == "bind" && mount["read_only"] && !mount.fetch("bind").fetch("create_host_path") }
    options = ipa.fetch("command")
    abort "IPA must remain CA-less without PKINIT" unless options.include?("--no-pkinit") && options.include?("--ca-cert-file=/bootstrap/ca.pem") && %w[http dirsrv].all? { |service| options.include?("--#{service}-cert-file=/bootstrap/fullchain.pem") && options.include?("--#{service}-cert-file=/bootstrap/key.pem") } && !options.include?("--external-ca")
    abort "Passwords must stay out of Compose" if options.any? { |option| option.match?(/\A--(?:admin|ds)-password/) }
    abort "Healthcheck must use persistent IPA trust" unless ipa.fetch("healthcheck").fetch("test").join(" ").include?("--cacert /etc/ipa/ca.crt")
    ports = ipa.fetch("ports").map { |port| [port.fetch("target"), port.fetch("protocol")] }
    abort "IPA must leave HTTP/HTTPS ports to Traefik" if ports.any? { |port, _| [80, 443].include?(port) }
    abort "IPA directory and Kerberos ports changed" unless ports.sort == [[88, "tcp"], [88, "udp"], [389, "tcp"], [464, "tcp"], [464, "udp"], [636, "tcp"]].sort
    abort "IPA must join both networks" unless ipa.fetch("networks").keys.sort == %w[infra traefik_proxy]
    proxy = config.fetch("networks").fetch("traefik_proxy")
    abort "Traefik network must be external" unless proxy.fetch("name") == "traefik_proxy" && proxy.fetch("external")
    expected = {
      "traefik.enable" => "true",
      "traefik.docker.network" => "traefik_proxy",
      "traefik.tcp.routers.freeipa-secure.entrypoints" => "https",
      "traefik.tcp.routers.freeipa-secure.rule" => "HostSNI(`ipa.home.arpa`)",
      "traefik.tcp.routers.freeipa-secure.tls" => "true",
      "traefik.tcp.routers.freeipa-secure.tls.passthrough" => "true",
      "traefik.tcp.services.freeipa-secure.loadbalancer.server.port" => "443"
    }
    abort "IPA TLS passthrough routing changed" unless ipa.fetch("labels") == expected
  '
