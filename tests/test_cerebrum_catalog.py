"""Verify the GPU replacement's Atlas identity and workload configuration."""

from pathlib import Path

import pytest

from puppet_catalog import ROOT, compile_catalog, resources
from test_profile_catalogs import DUMMY_SECRETS


@pytest.mark.parametrize("hostname", ["cognition", "cerebrum"])
def test_gpu_host_catalog(tmp_path: Path, hostname: str) -> None:
    """Use Atlas services and leave GPU workload lifetime to on-demand controllers."""
    result = resources(
        compile_catalog(
            tmp_path,
            (ROOT / "manifests/site.pp").read_text(),
            DUMMY_SECRETS,
            hostname=hostname,
            node_type="docker",
            fact_overrides={
                "os": {
                    "family": "Debian",
                    "name": "Ubuntu",
                    "architecture": "amd64",
                    "release": {"major": "26.04", "full": "26.04"},
                    "distro": {"codename": "resolute"},
                }
            },
        )
    )
    enrollment = result["Freeipa_client_enrollment[default]"]
    assert enrollment["hostname"] == f"{hostname}.home.arpa"
    assert enrollment["server"] == "ipa.home.arpa"
    assert enrollment["ca_cert_file"].endswith("atlas-infrastructure.crt")
    certificate = result["Vault_cert[codex]"]
    assert (
        certificate["vault_uri"]
        == "https://vault.home.arpa:8200/v1/pki_int/issue/puppet"
    )
    assert certificate["ca_trust"] == "/etc/ssl/certs/ca-certificates.crt"
    assert certificate["cert_data"]["common_name"] == f"codex.{hostname}.home.arpa"
    assert result["Nfs_config::Ipa_service[nfs]"]["ipa_server"] == "ipa.home.arpa"
    mount = result["Nfs::Client::Mount[/export/scratch]"]
    assert mount["server"] == "atlas.home.arpa"
    assert mount["atboot"] is False
    assert "sec=krb5p" in mount["options_nfsv4"]
    assert "loki.atlas.home.arpa" in result["File[/etc/alloy/config.alloy]"]["content"]
    alloy_unit = result[
        "File[/etc/systemd/system/alloy.service.d/alloy_settings.conf]"
    ]["content"]
    assert "--stability.level=experimental" in alloy_unit
    assert "Package[nvidia-driver-590-open]" in result
    assert result["File[/opt/docker]"]["ensure"] == "directory"
    assert not any(name.startswith("Apt::Ppa[") for name in result)
    assert not any(name.startswith("Apt_key[") for name in result)
    runtime = result["Exec[configure-nvidia-container-runtime]"]
    assert runtime["command"].endswith("--runtime=docker --set-as-default")
    assert runtime["require"] == "Package[nvidia-container-toolkit]"
    assert runtime["notify"] == "Service[docker]"
    power = result["File[/etc/systemd/system/nvidia-power-management.service]"][
        "content"
    ]
    assert "ExecCondition=/usr/bin/nvidia-smi -L" in power
    for project in ("vllm", "wolf"):
        for suffix in ("path", "service"):
            assert (
                result[f"File[/etc/systemd/system/{project}-deploy.{suffix}]"]["ensure"]
                == "absent"
            )
            unit = result[f"Service[{project}-deploy.{suffix}]"]
            assert unit["enable"] is False
            assert unit["ensure"] is False
    assert (ROOT / f"data/roles/nodes/{hostname}.home.arpa.yaml").is_symlink()
    if hostname == "cerebrum":
        assert (
            "MemoryMax=2G"
            in result["File[/etc/systemd/system/steam-dedup.service]"]["content"]
        )
        assert result["Systemd::Unit_file[steam-dedup.timer]"]["active"] is True
    assert "Class[Profile::Docker_node]" not in result
