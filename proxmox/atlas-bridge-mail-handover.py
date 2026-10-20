#!/usr/bin/env python3
"""Point Paperless at Atlas Bridge and authenticate mail clients without mail."""

from __future__ import annotations

import os
import socket
import subprocess

PAPERLESS = """
from paperless_mail.models import MailAccount
from paperless_mail.mail import get_mailbox, mailbox_login

account = MailAccount.objects.get(pk=1)
assert account.imap_server in {"docker.home.arpa", "atlas.home.arpa"}
assert account.imap_port == 10243
assert account.imap_security == MailAccount.ImapSecurity.SSL
mailbox = get_mailbox("atlas.home.arpa", account.imap_port, account.imap_security)
try:
    mailbox_login(mailbox, account)
finally:
    mailbox.logout()
MailAccount.objects.filter(pk=account.pk).update(imap_server="atlas.home.arpa")
"""
SMTP = """
from django.conf import settings
from django.core.mail import get_connection

assert settings.EMAIL_HOST == "atlas.home.arpa"
assert settings.EMAIL_PORT == 10125
assert settings.EMAIL_USE_SSL and not settings.EMAIL_USE_TLS
connection = get_connection(fail_silently=False)
try:
    assert connection.open()
finally:
    connection.close()
"""


def handover() -> None:
    """Check native client authentication and update only the IMAP hostname."""
    if os.geteuid() != 0 or socket.getfqdn() != "atlas.home.arpa":
        raise RuntimeError("Run as root on Atlas")
    for container, interpreter, manage, code in (
        (
            "send-bills-bills-1",
            ".venv/bin/python",
            "src/send_bills/manage.py",
            SMTP,
        ),
        (
            "paperless-ai-webserver-1",
            "python",
            "/usr/src/paperless/src/manage.py",
            PAPERLESS,
        ),
    ):
        result = subprocess.run(
            ["docker", "exec", "-i", container, interpreter, manage, "shell"],
            input=code,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode:
            raise RuntimeError("Mail authentication failed; diagnostics withheld")


if __name__ == "__main__":
    try:
        handover()
    except Exception:
        raise SystemExit("Mail handover failed; diagnostics withheld") from None
    print("Paperless IMAP and Bills SMTP authentication passed; no mail sent")
