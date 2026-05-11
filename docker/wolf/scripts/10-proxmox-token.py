#!/usr/bin/env python3

import json
import os
import subprocess
import sys
from shutil import which

PVE_USER = os.getenv("WOLF_PVE_USER", "wolf@pve")
PVE_TOKEN_ID = os.getenv("WOLF_PVE_TOKEN_ID", "wolf")
PVE_VM_ROLE_NAME = os.getenv("WOLF_PVE_VM_ROLE_NAME", "WolfVmControl")
PVE_NODE_ROLE_NAME = os.getenv("WOLF_PVE_NODE_ROLE_NAME", "WolfNodePower")
PVE_VM_ID = os.getenv("WOLF_VM_ID", "200")
PVE_NODE = os.getenv("WOLF_PROXMOX_NODE", "proxmox-cortex")


def run_pveum(
    *args: str, capture_output: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["pveum", *args],
        check=True,
        text=True,
        capture_output=capture_output,
    )


def load_entries(*args: str) -> list[dict[str, object]]:
    result = run_pveum(*args, "--output-format", "json")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"failed to parse pveum output for {' '.join(args)}"
        ) from error

    data: object
    if isinstance(payload, dict):
        data = payload.get("data", [])
    else:
        data = payload
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def entry_exists(entries: list[dict[str, object]], key: str, needle: str) -> bool:
    return any(entry.get(key) == needle for entry in entries)


def ensure_user() -> None:
    if entry_exists(load_entries("user", "list"), "userid", PVE_USER):
        return
    run_pveum("user", "add", PVE_USER, "--comment", "Wolf power control service")


def ensure_role(role_name: str, privs: str) -> None:
    if entry_exists(load_entries("role", "list"), "roleid", role_name):
        return
    run_pveum("role", "add", role_name, "--privs", privs)


def ensure_token() -> tuple[bool, str]:
    tokens = load_entries("user", "token", "list", PVE_USER)
    if entry_exists(tokens, "tokenid", PVE_TOKEN_ID):
        return False, ""

    print("Create or copy the token secret now:")
    result = run_pveum(
        "user",
        "token",
        "add",
        PVE_USER,
        PVE_TOKEN_ID,
        "-privsep",
        "1",
        "--output-format",
        "json",
    )
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict):
        raise RuntimeError("unexpected token payload from pveum")
    token_secret = payload.get("value")
    if not isinstance(token_secret, str):
        raise RuntimeError("token payload missing value")
    return True, token_secret


def apply_acls() -> None:
    run_pveum(
        "acl",
        "modify",
        f"/vms/{PVE_VM_ID}",
        "-token",
        f"{PVE_USER}!{PVE_TOKEN_ID}",
        "-role",
        PVE_VM_ROLE_NAME,
    )
    run_pveum(
        "acl",
        "modify",
        f"/nodes/{PVE_NODE}",
        "-token",
        f"{PVE_USER}!{PVE_TOKEN_ID}",
        "-role",
        PVE_NODE_ROLE_NAME,
    )


def main() -> int:
    if not which("pveum"):
        print("pveum not found", file=sys.stderr)
        return 127

    ensure_user()
    ensure_role(PVE_VM_ROLE_NAME, "VM.PowerMgmt VM.Console VM.Audit")
    ensure_role(PVE_NODE_ROLE_NAME, "Sys.PowerMgmt Sys.Audit")
    created, token_secret = ensure_token()
    apply_acls()

    if token_secret:
        print(
            "Store this full value in Vault at kv/wolf field proxmox-cortex-api-token:"
        )
        print(f"{PVE_USER}!{PVE_TOKEN_ID}={token_secret}")
    elif created:
        print(
            f"Store the full value {PVE_USER}!{PVE_TOKEN_ID}=<token-secret> in Vault at kv/wolf field proxmox-cortex-api-token."
        )
    else:
        print(
            f"Token {PVE_USER}!{PVE_TOKEN_ID} already exists; Proxmox will not show its secret again."
        )
        print(
            "If the secret is not already in Vault, remove and recreate the token or create a new token id."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
