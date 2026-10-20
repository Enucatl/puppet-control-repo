#!/usr/bin/env bash
set -euo pipefail

repo=$(cd "$(dirname "$0")/.." && pwd)
docker compose --env-file /dev/null -f "$repo/docker/freeipa/atlas-compose.yaml" config --format json |
  ruby -rjson -e '
    config = JSON.parse(STDIN.read)
    ipa = config.fetch("services").fetch("ipa")
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
