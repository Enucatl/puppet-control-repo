#!/usr/bin/env python3
"""Close the stopped source bot's local API session before transferring its state."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import urllib.parse
import urllib.request

TOKEN = Path("/opt/docker/crabberbot/secrets/telegram_bot_token")


def close_session() -> None:
    """Keep queued updates and close only the source local Telegram API session."""
    if os.geteuid() != 0 or socket.getfqdn() != "docker.home.arpa":
        raise RuntimeError("Run as root on docker.home.arpa")
    result = subprocess.run(
        [
            "docker",
            "inspect",
            "crabberbot-crabberbot-1",
            "crabberbot-telegram-bot-api-1",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError("Cannot inspect source bot containers")
    bot, api = json.loads(result.stdout)
    if bot["State"]["Running"] or not api["State"]["Running"]:
        raise RuntimeError("Stop the source bot and keep its local API running first")
    address = api["NetworkSettings"]["Networks"]["crabberbot_backend"]["IPAddress"]
    if not address:
        raise RuntimeError("Source local API has no backend address")
    token = TOKEN.read_text().strip()
    if not token or "\n" in token:
        raise RuntimeError("Source bot token is unavailable")
    for method, parameters in [
        ("deleteWebhook", {"drop_pending_updates": "false"}),
        ("close", {}),
    ]:
        request = urllib.request.Request(
            f"http://{address}:8081/bot{token}/{method}",
            data=urllib.parse.urlencode(parameters).encode(),
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.load(response)
        if payload.get("ok") is not True or payload.get("result") is not True:
            raise RuntimeError(
                f"Local Telegram API rejected {method}; details withheld"
            )


if __name__ == "__main__":
    try:
        close_session()
    except Exception as error:
        raise SystemExit(
            f"Local API close failed ({type(error).__name__}); credentials withheld"
        ) from None
    print("Source local Telegram session closed; pending updates preserved")
