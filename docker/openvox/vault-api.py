"""Execute a private Vault control request inside its own container."""

import http.client
import json
import ssl
import sys
from pathlib import Path

request = json.load(sys.stdin)
connection = http.client.HTTPSConnection(
    "localhost",
    8200,
    context=ssl.create_default_context(cafile="/state/trust.pem"),
    timeout=30,
)
headers = {"Content-Type": "application/json"}
if request.get("auth", True):
    headers["X-Vault-Token"] = json.loads(Path("/state/recovery.json").read_text())[
        "root_token"
    ]
connection.request(
    request["method"],
    "/v1/" + request["path"],
    json.dumps(request.get("body", {})),
    headers,
)
response = connection.getresponse()
body = response.read()
print(json.dumps({"status": response.status, "body": json.loads(body) if body else {}}))
