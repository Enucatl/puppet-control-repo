"""Keep CRL automation opt-in and verify its runnable service boundaries."""

import json

import pytest
from pathlib import Path

from puppet_catalog import compile_catalog, resources
from test_openvox_infra import compose_model as compose_model


ROOT = Path(__file__).resolve().parents[1]


def test_publisher_cannot_activate_other_services(compose_model: dict) -> None:
    """An explicit one-shot updates existing trust without activating other services."""
    service = compose_model["services"]["openvox-crl-refresh"]
    assert service["profiles"] == ["openvox-tools"]
    assert service["restart"] == "no"
    assert not service.get("depends_on")
    assert service["user"] == "999:999"
    assert service["read_only"] is True
    assert "ALL" in service["cap_drop"]
    assert service["command"] == ["/crl-code/crl-refresh.rb", "publish", "/config.json"]
    assert "VAULT_TOKEN" not in service.get("environment", {})
    mounts = {mount["target"]: mount for mount in service["volumes"]}
    assert not mounts["/trust"].get("read_only", False)
    assert {m["source"] for m in mounts.values() if m["type"] == "volume"} == {
        "openvox_trust"
    }
    assert mounts["/credentials"]["read_only"] is True
    assert mounts["/config.json"]["read_only"] is True
    assert not any(
        "docker.sock" in mount.get("source", "") for mount in mounts.values()
    )
    assert all(
        compose_model["volumes"][mount["source"]]["external"] is True
        for mount in mounts.values()
        if mount["type"] == "volume"
    )


def test_no_additional_permanent_crl_service_or_volumes(compose_model: dict) -> None:
    """Use existing CA distribution endpoints and Vault's built-in CRL scheduler."""
    assert {
        name for name in compose_model["services"] if name.startswith("openvox-crl")
    } == {"openvox-crl-refresh"}
    assert not any(name.startswith("openvox_crl") for name in compose_model["volumes"])


@pytest.mark.parametrize("credentials_id", [None, 999])
def test_publisher_and_client_catalog(
    tmp_path: Path, credentials_id: int | None
) -> None:
    """Compile both opt-in roles with ordered startup, recovery and alerts."""
    config = {
        "root": "/etc/puppetlabs/puppet/ssl/certs/ca.pem",
        "issuer": "/etc/puppetlabs/puppet/ssl/certs/ca.pem",
        "source_url": "https://docker.home.arpa:8140/puppet-ca/v1/certificate_revocation_list/ca",
        "root_crl_url": "https://hcv.home.arpa:8200/v1/pki/crl/pem",
        "output_file": "/etc/puppetlabs/puppet/ssl/crl.pem",
        "state_file": "/var/lib/openvox-crl/client-state.json",
        "status_file": "/var/lib/openvox-crl/client-status.json",
    }
    publisher_overrides = (
        {}
        if credentials_id is None
        else {
            "profile::openvox_crl_refresh::credentials_uid": credentials_id,
            "profile::openvox_crl_refresh::credentials_gid": credentials_id,
        }
    )
    catalog = compile_catalog(
        tmp_path,
        "class { 'systemd': dropin_files => {}, journald_settings => {} } "
        "include profile::openvox_crl_refresh, profile::openvox_crl_client",
        {
            "profile::openvox_crl_refresh::config": {
                "role_id_file": "/credentials/role-id",
                "secret_id_file": "/credentials/secret-id",
            },
            "profile::openvox_crl_client::config": config,
            "systemd::dropin_files": {},
            "systemd::journald_settings": {},
            **publisher_overrides,
        },
    )
    result = resources(catalog)
    publisher = result["File[/etc/systemd/system/openvox-crl-refresh.service]"][
        "content"
    ]
    assert "run --rm --no-deps --pull never openvox-crl-refresh" in publisher
    assert "WorkingDirectory=/opt/docker/puppet-control-repo" in publisher
    assert "-f docker/docker-compose.yml run" in publisher
    assert " compose up " not in publisher
    assert "OnFailure=openvox-crl-alert@openvox-crl-refresh.service" in publisher
    client = result["File[/etc/systemd/system/openvox-crl-client.service]"]["content"]
    assert "crl-refresh.rb sync /etc/openvox-crl/client.json" in client
    assert "Before=puppet.service" in client
    dropin = result["File[/etc/systemd/system/puppet.service.d/openvox-crl.conf]"][
        "content"
    ]
    assert "Wants=openvox-crl-client.service" in dropin
    assert "After=openvox-crl-client.service" in dropin
    assert "Requires=" not in dropin
    assert json.loads(result["File[/etc/openvox-crl/client.json]"]["content"]) == config
    for job in ("client", "refresh"):
        timer = result[f"File[/etc/systemd/system/openvox-crl-{job}.timer]"]["content"]
        assert "OnBootSec=1min" in timer
        assert "OnUnitActiveSec=5min" in timer
        service = result[f"Service[openvox-crl-{job}.timer]"]
        assert service["enable"] is True
        assert service["ensure"] is True
    alert = result["File[/etc/systemd/system/openvox-crl-alert@.service]"]["content"]
    assert "StartLimitIntervalSec=1h" in alert
    assert "StartLimitBurst=1" in alert
    assert "/usr/sbin/sendmail -t" in alert
    credentials = result["File[/etc/openvox-crl/credentials]"]
    assert credentials["mode"] == "0700"
    expected_id = 100999 if credentials_id is None else credentials_id
    assert credentials["owner"] == credentials["group"] == expected_id
    assert (
        f"ExecStartPre=/usr/bin/install -o {expected_id} -g {expected_id} -m 0600 "
        '"/etc/puppetlabs/puppet/ssl/certs/docker.home.arpa.pem" '
        "/etc/openvox-crl/credentials/client.crt"
    ) in publisher
    assert (
        f"ExecStartPre=/usr/bin/install -o {expected_id} -g {expected_id} -m 0600 "
        '"/etc/puppetlabs/puppet/ssl/private_keys/docker.home.arpa.pem" '
        "/etc/openvox-crl/credentials/client.key"
    ) in publisher


def test_crl_jobs_have_no_live_hiera_enablement() -> None:
    """Adding the classes cannot silently enable them on existing hosts."""
    for path in (ROOT / "data").rglob("*.yaml"):
        assert "profile::openvox_crl" not in path.read_text(), path
