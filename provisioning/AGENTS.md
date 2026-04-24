## Router provisioning notes

- Treat post-apply smoke checks as part of the normal router configuration workflow.
- Changes that affect DNS, WAN, containers, Suricata, or IPv6 should be validated with `--tags apply` or `--tags healthcheck`.
- Keep edits surgical. Do not refactor unrelated provisioning logic when adding checks.
