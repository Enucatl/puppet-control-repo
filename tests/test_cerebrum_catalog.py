"""Verify the GPU replacement's Atlas identity and workload configuration."""

from pathlib import Path

from puppet_catalog import ROOT, compile_catalog, resources
from test_profile_catalogs import DUMMY_SECRETS


def test_gpu_host_catalog(tmp_path: Path) -> None:
    """Use Atlas services and leave GPU workload lifetime to on-demand controllers."""
    hostname = "cerebrum"
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
    assert (ROOT / f"data/roles/nodes/{hostname}.home.arpa.yaml").is_symlink()
    assert (
        "MemoryMax=2G"
        in result["File[/etc/systemd/system/steam-dedup.service]"]["content"]
    )
    assert result["Systemd::Unit_file[steam-dedup.timer]"]["active"] is True
    assert "Class[Profile::Docker_node]" not in result

    prune = result[
        "File[/etc/systemd/system/docker-prune.service.d/retain-paddle.conf]"
    ]["content"]
    assert result["Systemd::Dropin_file[retain-paddle.conf]"]["notify_service"] is False
    assert "ExecStart=\nExecStart=" in prune
    assert (
        "paddleocr-genai-vllm-server@sha256:bffd525308facf5dba2f8eca44ab476704a0ae3bfdcba25f77655973e4c0a7ca"
        in prune
    )
    assert "docker create --pull=never" in prune
    assert "--network none --userns host --entrypoint /bin/true" in prune
    assert "{{.Image}}" in prune and "{{.Id}}" in prune
    assert "{{.Config.Labels.puppet_keep_paddle_recognition}}" in prune
    assert (
        "system prune --all --force --filter until=24h --filter label!=puppet_keep_paddle_recognition=true"
        in prune
    )
    assert result["Systemd::Unit_file[docker-prune.timer]"]["active"] is True
    timer = result["File[/etc/systemd/system/docker-prune.timer]"]["content"]
    assert "OnCalendar=*-*-* 03:00:00 UTC" in timer
