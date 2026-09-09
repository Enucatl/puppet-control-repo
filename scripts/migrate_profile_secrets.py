"""Copy the four renamed Hiera fields in kv/puppet; retain legacy fields."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

KEYS = {
    "profile::docker_host::maxmind_account_id": "profile::alloy::maxmind_account_id",
    "profile::docker_host::maxmind_license_key": "profile::alloy::maxmind_license_key",
    "profile::docker_host::printer_smb_password": "profile::docker_node::printer_smb_password",
    "profile::docker_host::pictures_smb_password": "profile::docker_node::pictures_smb_password",
}


def check_deployed_configuration(environment: Path) -> None:
    """Do not expose canonical Samba strings to an older production catalog."""
    common = environment / "data/common.yaml"
    if not common.is_file():
        raise RuntimeError(f"Deployed Hiera data not found: {common}")
    result = subprocess.run(
        [
            "/opt/puppetlabs/puppet/bin/ruby",
            "-ryaml",
            "-rjson",
            "-e",
            "puts JSON.generate(YAML.load_file(ARGV[0]).fetch('lookup_options', {}))",
            str(common),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode:
        raise RuntimeError("Cannot read deployed Hiera lookup options")
    options = json.loads(result.stdout)
    for key in KEYS.values():
        if (
            key.startswith("profile::docker_node::")
            and options.get(key, {}).get("convert_to") != "Sensitive"
        ):
            raise RuntimeError(
                f"Deploy the Sensitive conversion for {key} before copying Vault fields"
            )


def migration_patch(data: dict[str, Any]) -> dict[str, Any]:
    """Existing canonical values win, including intentional overrides."""
    patch = {}
    for old, new in KEYS.items():
        if new in data:
            continue
        if old not in data:
            raise ValueError(f"Missing both {old} and {new}")
        patch[new] = data[old]
    return patch


def vault(*args: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    result = subprocess.run(
        ["vault", "kv", *args],
        input=json.dumps(payload) if payload is not None else None,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode:
        # Vault error output may contain request data; do not echo it.
        raise RuntimeError(
            f"Vault {args[0]} failed (exit {result.returncode}); no secret values logged"
        )
    return json.loads(result.stdout)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Copy missing canonical fields using CAS"
    )
    parser.add_argument(
        "--deployed-environment",
        type=Path,
        default=Path("/etc/puppetlabs/code/environments/production"),
        help="Deployed environment to check before writing canonical Samba keys",
    )
    args = parser.parse_args()
    if args.apply:
        check_deployed_configuration(args.deployed_environment)
    current = vault("get", "-format=json", "kv/puppet")["data"]
    patch = migration_patch(current["data"])
    for old, new in KEYS.items():
        status = "copy" if new in patch else "already present; preserve"
        print(f"{old} -> {new}: {status}")
    if not patch or not args.apply:
        return
    vault(
        "patch",
        "-format=json",
        f"-cas={current['metadata']['version']}",
        "kv/puppet",
        "-",
        payload=patch,
    )
    verified = vault("get", "-format=json", "kv/puppet")["data"]["data"]
    if any(verified.get(key) != value for key, value in patch.items()):
        raise RuntimeError("Post-migration verification failed; legacy fields retained")
    print("Canonical fields verified; legacy fields retained for rollback.")


if __name__ == "__main__":
    main()
