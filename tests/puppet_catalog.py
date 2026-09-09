from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUBY = Path("/opt/puppetlabs/puppet/bin/ruby")
DEPENDENCIES = Path(
    os.environ.get(
        "PUPPET_TEST_MODULEPATH", "/etc/puppetlabs/code/environments/production/modules"
    )
)


def compile_catalog(
    workdir: Path,
    code: str,
    data: dict[str, Any] | None = None,
    *,
    hostname: str = "docker",
    node_type: str = "docker",
    root: Path = ROOT,
) -> dict[str, Any]:
    if not DEPENDENCIES.is_dir():
        pytest.skip(
            "Puppet Forge dependencies are not installed; set PUPPET_TEST_MODULEPATH "
            "to a populated module directory"
        )
    workdir.mkdir(parents=True, exist_ok=True)
    overrides = workdir / "overrides.json"
    overrides.write_text(json.dumps(data or {}))
    hiera = workdir / "hiera.yaml"
    hiera.write_text(
        json.dumps(
            {
                "version": 5,
                "defaults": {"data_hash": "yaml_data", "datadir": str(root / "data")},
                "hierarchy": [
                    {"name": "Test overrides, never Vault", "path": str(overrides)},
                    {"name": "Node", "path": "nodes/%{trusted.hostname}.yaml"},
                    {"name": "Role", "path": "roles/%{facts.node_type}.yaml"},
                    {
                        "name": "OS",
                        "path": "os/%{facts.os.family}/%{facts.os.name}.yaml",
                    },
                    {"name": "Family", "path": "os/%{facts.os.family}.yaml"},
                    {"name": "Common", "path": "common.yaml"},
                ],
            }
        )
    )
    debian = node_type == "proxmox"
    facts = {
        "fqdn": f"{hostname}.home.arpa",
        "hostname": hostname,
        "domain": "home.arpa",
        "ipaddress": "192.0.2.10",
        "clientcert": f"{hostname}.home.arpa",
        "networking": {
            "fqdn": f"{hostname}.home.arpa",
            "hostname": hostname,
            "domain": "home.arpa",
            "ip": "192.0.2.10",
            "interfaces": {
                "eth0": {"ip": "192.0.2.10", "bindings": [{"address": "192.0.2.10"}]}
            },
        },
        "os": {
            "family": "Debian",
            "name": "Debian" if debian else "Ubuntu",
            "architecture": "amd64",
            "release": {
                "major": "13" if debian else "24.04",
                "full": "13.0" if debian else "24.04",
            },
            "distro": {"codename": "trixie" if debian else "noble"},
        },
        "kernel": "Linux",
        "architecture": "amd64",
        "node_type": node_type,
        "is_virtual": not debian,
        "virtual": "physical" if debian else "kvm",
        "service_provider": "systemd",
        "systemd": True,
        "systemd_version": "257" if debian else "255",
        "processors": {"count": 4},
        "memory": {"system": {"total_bytes": 8589934592}},
        "puppetversion": "8.21.0",
        "path": "/usr/bin:/bin:/usr/sbin:/sbin",
    }
    result = subprocess.run(
        [str(RUBY), str(ROOT / "tests/puppet/compile.rb")],
        input=json.dumps(
            {
                "workdir": str(workdir),
                "hiera_config": str(hiera),
                "code": code,
                "certname": f"{hostname}.home.arpa",
                "facts": facts,
                "modulepath": [str(root / "modules"), str(DEPENDENCIES)],
            }
        ),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, "\n".join(
        (result.stderr + result.stdout).splitlines()[:8]
    )
    return json.loads(result.stdout)


def resources(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        f"{r['type']}[{r['title']}]": r.get("parameters", {})
        for r in catalog["resources"]
    }
