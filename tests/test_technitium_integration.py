"""Keep normal router deployments consistent with the DNS cutover state."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest

yaml = pytest.importorskip("yaml")
ROOT = Path(__file__).parents[1] / "provisioning"


def test_router_deployment_modes(tmp_path: Path) -> None:
    """Normal deployment must contain only the active DNS provider."""
    active = True
    if not shutil.which("ansible-playbook"):
        pytest.skip("Use the provisioning environment for Ansible rendering")
    # Give includes the same template search root as the router playbook.
    (tmp_path / "templates").symlink_to(ROOT / "templates", target_is_directory=True)
    templates = {
        "containers": "partials/containers.j2",
        "deletes": "partials/deletes.j2",
        "smoke": "app_configs/router-smoke-check.sh.j2",
        "alloy": "app_configs/alloy-vyos.alloy.j2",
    }
    play = [
        {
            "hosts": "localhost",
            "connection": "local",
            "gather_facts": False,
            "vars_files": [str(ROOT / "host_vars/router.yml")],
            "vars": {"ipv6_prefix": "2001:db8:1234"},
            "tasks": [
                {
                    "ansible.builtin.template": {
                        "src": template,
                        "dest": str(tmp_path / name),
                    },
                }
                for name, template in templates.items()
            ],
        }
    ]
    path = tmp_path / "render.yml"
    path.write_text(yaml.safe_dump(play))
    result = subprocess.run(
        [
            "ansible-playbook",
            "-i",
            "localhost,",
            str(path),
            "-e",
            '{"technitium_active": ' + str(active).lower() + "}",
        ],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    containers, deletes, smoke, alloy = (
        (tmp_path / name).read_text() for name in templates
    )
    subprocess.run(["sh", "-n"], input=smoke, text=True, check=True)
    assert (
        "volume config source /config/containers/technitium-candidate" in containers
    ) == active
    assert ("set container name technitium capability net-raw" in containers) == active
    assert ('AUTHORITATIVE_PORT="53"' in smoke) == active
    assert ("Technitium container running" in smoke) == active
    for provider in ["adguard", "dnsmasq"]:
        assert (f"set container name {provider} " in containers) != active
        assert (f"delete container name {provider}\n" in deletes) == active
        assert (f'job      = "{provider}"' in alloy) != active
    assert ("AdGuard container running" in smoke) != active
    assert ("dnsmasq container running" in smoke) != active
    assert "Alloy HTTP readiness" in smoke
    assert 'job      = "suricata"' in alloy
    if active:
        assert 'loki.source.file "technitium" {\n  targets    = local.file_match.technitium.targets\n  forward_to = [loki.write.central_alloy.receiver]' in alloy

    output = os.environ.get("TECHNITIUM_ROLLBACK_CONFIG")
    if active and output:
        directory = Path(output) / "alloy"
        directory.mkdir(exist_ok=True)
        (directory / "config.alloy").write_text(alloy)


def test_retired_mode_is_rejected(tmp_path: Path) -> None:
    guard = yaml.safe_load((ROOT / "tasks/13_technitium_guard.yml").read_text())[0]
    play = [{"hosts": "localhost", "connection": "local", "gather_facts": False,
             "vars": {"technitium_active": False}, "tasks": [guard]}]
    path = tmp_path / "guard.yml"
    path.write_text(yaml.safe_dump(play))
    result = subprocess.run(["ansible-playbook", "-i", "localhost,", str(path)],
                            text=True, capture_output=True)
    assert result.returncode != 0
    assert "Normal provisioning requires active Technitium" in result.stdout
