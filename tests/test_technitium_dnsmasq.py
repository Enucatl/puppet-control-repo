"""Export a realistic rollback configuration for the dnsmasq image to validate."""

import os
from pathlib import Path

import pytest

from test_technitium import rendered  # noqa: F401
from test_technitium_leases import leases


def test_rollback_config(rendered: dict) -> None:
    """Combine production config with both old reservations and a new lease."""
    destination = os.environ.get("TECHNITIUM_ROLLBACK_CONFIG")
    if not destination:
        pytest.skip("Run tests/run-technitium.sh for the real dnsmasq parser")
    scope = rendered["scopes"][0]
    result = leases.merge(
        rendered["scopes"],
        f"0 02:00:00:aa:bb:cc {scope['start']} migrated *\n",
        [],
        None,
        100,
    )
    directory = Path(destination)
    (directory / "dnsmasq.conf").write_text(rendered["dnsmasq"])
    (directory / "continuity.conf").write_text(result["dnsmasq"])
