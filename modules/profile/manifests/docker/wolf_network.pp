# Directed broadcasts used by Wolf; included on the existing Docker-role hosts.
class profile::docker::wolf_network {
  $wolf_wol_magic_hex = 'ffffffffffff30560f5ea9de30560f5ea9de30560f5ea9de30560f5ea9de30560f5ea9de30560f5ea9de30560f5ea9de30560f5ea9de30560f5ea9de30560f5ea9de30560f5ea9de30560f5ea9de30560f5ea9de30560f5ea9de30560f5ea9de30560f5ea9de'
  $wolf_wol_rule = "-o eth0 -d 10.0.0.255/32 -p udp --dport 9 -m length --length 130 -m string --algo bm --hex-string '|${wolf_wol_magic_hex}|' -j ACCEPT"

  exec { 'allow-wolf-wol-directed-broadcast':
    command => "/usr/sbin/iptables -I DOCKER-USER 1 ${wolf_wol_rule}",
    unless  => "/usr/sbin/iptables -C DOCKER-USER ${wolf_wol_rule}",
    path    => ['/usr/sbin', '/usr/bin', '/sbin', '/bin'],
    require => Service['docker'],
  }

  exec { 'enable-docker-bridge-directed-broadcast':
    command => '/bin/sh -c \'for setting in /proc/sys/net/ipv4/conf/br-*/bc_forwarding; do [ -e "$setting" ] && echo 1 > "$setting"; done\'',
    unless  => '/bin/sh -c \'for setting in /proc/sys/net/ipv4/conf/br-*/bc_forwarding; do [ -e "$setting" ] || exit 0; [ "$(cat "$setting")" = "1" ] || exit 1; done\'',
    require => Service['docker'],
  }

}
