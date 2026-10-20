#!/usr/bin/env python3
"""Authorize only Atlas Airflow's client certificate to read its existing KV."""

import importlib.util
import os
from pathlib import Path
import socket
from urllib.parse import urlsplit

SPEC = importlib.util.spec_from_file_location(
    "atlas_infra", Path(__file__).with_name("atlas-infra.py")
)
assert SPEC and SPEC.loader
infra = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(infra)


def authorize():
    """Use the established Atlas authority and the legacy Airflow policy scope."""
    assert os.geteuid() == 0 and socket.getfqdn() == "atlas.home.arpa"
    policy = """path "kv/data/airflow/connections/*" {
  capabilities = ["read", "list"]
}
path "kv/data/airflow/variables/*" {
  capabilities = ["read", "list"]
}
"""
    infra.api("PUT", "sys/policies/acl/airflow", {"policy": policy})
    infra.api(
        "POST",
        "auth/cert/certs/airflow",
        {
            "certificate": (infra.STATE / "root.pem").read_text(),
            "allowed_common_names": ["airflow.atlas.home.arpa"],
            "allowed_dns_sans": ["airflow.atlas.home.arpa"],
            "token_policies": ["airflow"],
            "token_ttl": "15m",
        },
    )
    role = infra.api("GET", "auth/cert/certs/airflow")["data"]
    assert role["allowed_dns_sans"] == ["airflow.atlas.home.arpa"]
    assert role["allowed_common_names"] == ["airflow.atlas.home.arpa"]
    assert role["token_policies"] == ["airflow"]
    connection = infra.api("GET", "kv/data/airflow/connections/djangodev")["data"]
    values = connection["data"]
    uri = urlsplit(values["uri"])
    assert uri.hostname in ("postgres.docker.home.arpa", "postgres-outputs")
    if uri.hostname != "postgres-outputs":
        credentials = uri.netloc.rsplit("@", 1)[0] + "@" if "@" in uri.netloc else ""
        port = ":" + str(uri.port) if uri.port is not None else ""
        values["uri"] = uri._replace(
            netloc=credentials + "postgres-outputs" + port
        ).geturl()
        infra.api(
            "POST",
            "kv/data/airflow/connections/djangodev",
            {
                "options": {"cas": connection["metadata"]["version"]},
                "data": values,
            },
        )
    print(
        "Atlas Airflow certificate role, scoped KV policy and outputs endpoint configured"
    )


if __name__ == "__main__":
    authorize()
