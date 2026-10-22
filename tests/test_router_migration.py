"""Check that route handover removes old hops without changing container prefixes."""

from pathlib import Path
import re

import yaml


PROVISIONING = Path(__file__).resolve().parents[1] / "provisioning"


def test_container_routes_replace_the_old_hosts_in_both_address_spaces() -> None:
    """Avoid ECMP toward retired hosts when applying the migrated IPv6 routes."""
    variables = yaml.safe_load((PROVISIONING / "host_vars/router.yml").read_text())
    variables["ipv6_prefix"] = "2001:db8:1234"

    def render(name: str) -> str:
        return re.sub(
            r"{{ (\w+) }}",
            lambda match: variables[match[1]],
            (PROVISIONING / "templates/partials" / name).read_text(),
        )

    routes = {
        parts[4]: (parts[6], parts[8])
        for line in render("nat_routing.j2").splitlines()
        if line.startswith("set protocols static route6 ")
        for parts in [line.split()]
    }
    deletes = set(render("deletes.j2").splitlines())
    expected = {}
    for prefix in (variables["ipv6_prefix"], variables["ula_prefix"]):
        for subnet, length, node, old_node in (
            ("a000", 56, "atlas", "docker"),
            ("a100", 64, "atlas", "docker"),
            ("a200", 56, "aperture", "desktop"),
            ("a300", 64, "aperture", "desktop"),
            ("c000", 56, "cerebrum", "cognition"),
            ("c100", 64, "cerebrum", "cognition"),
        ):
            network = f"{prefix}:{subnet}::/{length}"
            hop = f"fe80{variables[node + '_node_ipv6_suffix']}"
            interface = variables[
                "cortex_interface" if node == "cerebrum" else "lan_interface"
            ]
            expected[network] = (hop, interface)
            if old_node:
                old_hop = f"fe80{variables[old_node + '_node_ipv6_suffix']}"
                assert (
                    f"delete protocols static route6 {network} next-hop {old_hop}"
                    in deletes
                )
    for subnet, length in (("c000", 56), ("c100", 64)):
        network = f"2a02:168:6271:{subnet}::/{length}"
        expected[network] = (
            f"fe80{variables['cerebrum_node_ipv6_suffix']}",
            variables["cortex_interface"],
        )
        assert (
            f"delete protocols static route6 {network} next-hop fe80{variables['cognition_node_ipv6_suffix']}"
            in deletes
        )
    assert routes == expected


def test_logging_and_pxe_use_atlas() -> None:
    """Keep router log resolution and PXE replies off the retired Docker host."""
    variables = yaml.safe_load((PROVISIONING / "host_vars/router.yml").read_text())
    assert variables["loki_url"] == "https://loki.atlas.home.arpa/loki/api/v1/push"
    assert (
        variables["central_alloy_url"]
        == "http://alloy.atlas.home.arpa:12345/loki/api/v1/push"
    )
    system = (PROVISIONING / "templates/partials/system.j2").read_text()
    deletes = (PROVISIONING / "templates/partials/deletes.j2").read_text()
    for name in ("loki", "alloy"):
        assert (
            f"set system static-host-mapping host-name {name}.atlas.home.arpa inet {{{{ atlas_node_ip }}}}"
            in system
        )
        assert (
            f"delete system static-host-mapping host-name {name}.docker.home.arpa"
            in deletes
        )
    dnsmasq = (PROVISIONING / "templates/app_configs/dnsmasq.conf.j2").read_text()
    assert "dhcp-boot=tag:lan,iventoy_loader_16000_uefi,,{{ atlas_node_ip }}" in dnsmasq
    alloy = (PROVISIONING / "templates/app_configs/alloy-vyos.alloy.j2").read_text()
    assert "insecure_skip_verify" not in alloy

    assert variables["alloy_image"] == "docker.io/grafana/alloy:v1.21.0"
    assert 'ca_file = "/etc/ssl/certs/ca-certificates.crt"' in alloy
    tasks = (PROVISIONING / "tasks/02_push_files.yml").read_text()
    assert "src: /etc/ssl/certs/ca-certificates.crt" in tasks
    assert "{{ alloy_data_path }}/conf/ca-certificates.crt" in tasks
    assert "atlas-infrastructure.crt" not in tasks
    containers = (PROVISIONING / "templates/partials/containers.j2").read_text()
    assert (
        "set container name alloy volume alloy_ca destination /etc/ssl/certs/ca-certificates.crt"
        in containers
    )
    assert "set container name alloy volume alloy_ca mode ro" in containers
    assert (
        "set container name alloy volume alloy_ca source {{ alloy_data_path }}/conf/ca-certificates.crt"
        in containers
    )
    assert "--disable-reporting --stability.level=experimental'" in containers
    assert "CUSTOM_ARGS" not in containers
    smoke = (
        PROVISIONING / "templates/app_configs/router-smoke-check.sh.j2"
    ).read_text()
    assert 'retry_success "Alloy HTTP readiness"' in smoke
