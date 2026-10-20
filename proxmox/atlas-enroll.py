#!/usr/bin/env python3
"""Enroll Atlas into its established OpenVox authority and apply its catalog."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import socket

SPEC = importlib.util.spec_from_file_location(
    "atlas_infra", Path(__file__).with_name("atlas-infra.py")
)
assert SPEC and SPEC.loader
infra = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(infra)
CERTNAME = "atlas.home.arpa"
SYSTEM_CA = Path("/usr/local/share/ca-certificates/atlas-infrastructure.crt")
PUPPET_CA = Path("/etc/puppetlabs/puppet/ssl/certs/ca.pem")
CERTIFICATE = Path("/etc/puppetlabs/puppet/ssl/certs/atlas.home.arpa.pem")


def configure_runtime() -> None:
    """Grant certificate issuance scoped to the authenticated agent identity."""
    accessor = infra.api("GET", "sys/auth")["data"]["cert/"]["accessor"]
    identity = "{{identity.entity.aliases." + accessor + ".name}}"
    policy = f"""path "pki_int/issue/puppet" {{
  capabilities = ["create", "update"]
}}
path "pki_int/issue/puppet-{identity}" {{
  capabilities = ["create", "update"]
}}
path "auth/token/lookup-self" {{
  capabilities = ["read"]
}}
"""
    for name, domains, template, wildcards in (
        ("puppet", [identity], True, False),
        ("puppet-" + CERTNAME, [CERTNAME], False, True),
    ):
        infra.api(
            "POST",
            "pki_int/roles/" + name,
            {
                "key_type": "ml-dsa",
                "parameter_set": "ml-dsa-44",
                "allowed_domains": domains,
                "allowed_domains_template": template,
                "allow_bare_domains": True,
                "allow_subdomains": True,
                "allow_wildcard_certificates": wildcards,
                "allow_glob_domains": False,
                "allow_any_name": False,
                "allow_localhost": False,
                "allow_ip_sans": False,
                "cn_validations": ["hostname"],
                "key_usage": ["DigitalSignature"],
                "max_ttl": "2160h",
            },
        )
    infra.api("PUT", "sys/policies/acl/puppet", {"policy": policy})
    infra.api(
        "POST",
        "auth/cert/certs/puppet",
        {
            "certificate": infra.openvox("/bin/cat", "/trust/openvox-chain.pem"),
            "allowed_common_names": [CERTNAME],
            "allowed_dns_sans": [CERTNAME],
            "token_policies": ["puppet"],
            "token_no_default_policy": True,
            "token_ttl": "15m",
        },
    )


def install_trust() -> None:
    """Install authenticated public trust without replacing another authority."""
    chain = infra.openvox("/bin/cat", "/trust/openvox-chain.pem")
    root = (infra.STATE / "root.pem").read_text()
    for path, contents in ((PUPPET_CA, chain), (SYSTEM_CA, root)):
        if path.exists() and path.read_text() != contents:
            raise RuntimeError(
                f"Different existing trust requires explicit migration: {path}"
            )
    for path, contents in ((PUPPET_CA, chain), (SYSTEM_CA, root)):
        path.parent.mkdir(parents=True, exist_ok=True)
        infra.save(path, contents)
        path.chmod(0o644)
    infra.run(["update-ca-certificates"])


def enroll() -> None:
    """Use a single-use token through stdin; let Puppet install the IPA client."""
    checkpoint = infra.STATE / "freeipa/imported.json"
    if (
        not checkpoint.is_file()
        or json.loads(checkpoint.read_text()).get("success") is not True
    ):
        raise RuntimeError("Complete the verified final IPA import before enrollment")
    helper = str(infra.SOURCE / "proxmox/configure-puppet.sh")
    infra.run(["bash", helper, "--preflight", CERTNAME])
    install_trust()
    infra.run(["bash", str(infra.SOURCE / "proxmox/install-openvox.sh")])
    configure_runtime()
    if CERTIFICATE.exists():
        log = infra.run(["bash", helper, "--apply", CERTNAME, "openvox.home.arpa"])
    else:
        token = infra.api(
            "POST",
            "auth/token/create-orphan",
            {
                "policies": ["puppet-enrollment"],
                "type": "service",
                "ttl": "2h",
                "num_uses": 1,
                "renewable": False,
                "no_default_policy": True,
                "meta": {"certname": CERTNAME},
            },
        )["auth"]
        if (
            token["policies"] != ["puppet-enrollment"]
            or token["metadata"] != {"certname": CERTNAME}
            or token["num_uses"] != 1
            or not token["orphan"]
            or token["renewable"]
            or token["token_type"] != "service"
            or not 0 < token["lease_duration"] <= 7200
        ):
            raise RuntimeError("Vault returned an invalid enrollment token")
        log = infra.run(
            ["bash", helper, "docker", "-", CERTNAME, "openvox.home.arpa"],
            token["client_token"] + "\n",
        )
    infra.save(infra.STATE / "atlas-enrollment.log", log)
    infra.save(
        infra.STATE / "atlas-enrolled.json",
        json.dumps(
            {
                "certname": CERTNAME,
                "control_revision": infra.git("rev-parse", "HEAD").strip(),
            }
        ),
    )
    print(
        "Atlas enrollment and its first catalog passed; private log: /root/openvox-infra/atlas-enrollment.log"
    )


def main() -> None:
    """Restrict authority changes to the promoted Atlas host and versioned code."""
    os.umask(0o077)
    if os.geteuid() != 0 or socket.getfqdn() != CERTNAME:
        raise RuntimeError("Run as root on atlas.home.arpa")
    if (
        Path("/etc/openvox-infra/promoted").read_text().strip()
        != "openvox-authority:" + CERTNAME
    ):
        raise RuntimeError("Atlas is not the promoted authority")
    if (
        infra.run(infra.DOCKER + ["info", "--format", "{{.Name}}"]).strip()
        != socket.gethostname()
    ):
        raise RuntimeError("Refusing a nonlocal Docker daemon")
    if infra.git("status", "--porcelain").strip():
        raise RuntimeError("Run from the clean published control checkout")
    with infra.maintenance():
        enroll()


if __name__ == "__main__":
    main()
