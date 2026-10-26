"""Authenticated requests to the router's loopback-only Technitium API."""

import json
import time
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class Api:
    """Use one short-lived admin session for a configuration or recovery run."""

    def __init__(self, password: str, base_url: str = "http://127.0.0.1:5380/api/"):
        """Store connection details without opening a session."""
        self.password = password
        self.base_url = base_url
        self.token = ""

    def __call__(self, path: str, **parameters: object) -> dict:
        """Submit form data and reject HTTP and application-level failures."""
        parameters = {
            name: str(value).lower() if isinstance(value, bool) else value
            for name, value in parameters.items()
        }
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        request = Request(
            self.base_url + path, data=urlencode(parameters).encode(), headers=headers
        )
        with urlopen(request, timeout=30) as response:
            result = json.load(response)
        if result.get("status") != "ok":
            raise RuntimeError(
                f"{path}: {result.get('errorMessage', result.get('status'))}"
            )
        return result

    def __enter__(self) -> "Api":
        """Wait for startup, then authenticate once."""
        for attempt in range(30):
            try:
                with urlopen(self.base_url + "status", timeout=2) as response:
                    json.load(response)
                break
            except (URLError, TimeoutError):
                if attempt == 29:
                    raise
                time.sleep(1)
        self.token = self("user/login", user="admin", **{"pass": self.password})[
            "token"
        ]
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        """Close the session without hiding an earlier configuration failure."""
        try:
            self("user/logout")
        except (URLError, TimeoutError, RuntimeError):
            if exc_type is None:
                raise
