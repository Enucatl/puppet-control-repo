from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[1] / "modules/profile/files/codex-plugins-sync"
)


def release(version: str, days_old: int) -> dict[str, object]:
    """Build a GitHub release fixture with a stable publication timestamp."""
    published = datetime.now(timezone.utc) - timedelta(days=days_old)
    return {
        "tag_name": f"v{version}",
        "published_at": published.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "draft": False,
        "prerelease": False,
    }


@pytest.fixture
def plugin_home(tmp_path: Path) -> Path:
    """Create fake plugin clients and GitHub in an isolated home directory."""
    home = tmp_path / "home"
    fake_bin = home / ".local/bin"
    fake_bin.mkdir(parents=True)
    nvm_dir = home / ".nvm"
    nvm_dir.mkdir()
    (nvm_dir / "nvm.sh").write_text("nvm() { return 0; }\n")

    curl = fake_bin / "curl"
    curl.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
headers=''
response=''
while [ "$#" -gt 0 ]; do
  case "$1" in
    -D) headers="$2"; shift 2 ;;
    -o) response="$2"; shift 2 ;;
    *) shift ;;
  esac
done
count_file="$HOME/curl-count"
count="$(cat "$count_file" 2>/dev/null || printf '0')"
printf '%s\\n' "$((count + 1))" > "$count_file"
status="${FAKE_HTTP_STATUS:-200}"
if [ "$status" = 200 ]; then
  cp "$HOME/releases-fixture.json" "$response"
  printf 'ETag: "fixture"\\r\\n' > "$headers"
elif [ "$status" = 304 ]; then
  : > "$response"
  : > "$headers"
else
  exit 7
fi
printf '%s' "$status"
"""
    )
    curl.chmod(0o755)

    codex = fake_bin / "codex"
    codex.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
state="$HOME/codex-state.json"
if [ "$1 $2" = 'plugin list' ]; then
  if [ -s "$state" ]; then
    jq -n --slurpfile plugin "$state" '{installed: $plugin, available: []}'
  else
    printf '{"installed":[],"available":[]}\\n'
  fi
elif [ "$1 $2" = 'plugin add' ]; then
  catalog="$HOME/.local/share/codex/ponytail-marketplace/.agents/plugins/marketplace.json"
  version="$(jq -r '.plugins[0].source.ref | ltrimstr("v")' "$catalog")"
  jq -n --arg version "$version" \
    '{pluginId: "ponytail@ponytail", version: $version, enabled: true}' > "$state"
  count_file="$HOME/add-count"
  count="$(cat "$count_file" 2>/dev/null || printf '0')"
  printf '%s\\n' "$((count + 1))" > "$count_file"
else
  exit 2
fi
"""
    )
    codex.chmod(0o755)

    claude = fake_bin / "claude"
    claude.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
state="$HOME/claude-state.json"
actions="$HOME/claude-actions"
if [ "$1 $2" = 'plugin marketplace' ] && [ "$3" = add ]; then
  touch "$HOME/claude-marketplace-added"
  printf 'marketplace add\\n' >> "$actions"
elif [ "$1 $2" = 'plugin marketplace' ] && [ "$3" = list ]; then
  if [ -f "$HOME/claude-marketplace-added" ]; then
    jq -n --arg path "$HOME/.local/share/claude/ponytail-marketplace" \\
      '[{name: "ponytail", path: $path}]'
  else
    printf '[]\\n'
  fi
elif [ "$1 $2" = 'plugin list' ]; then
  if [ -s "$state" ]; then
    jq -n --slurpfile plugin "$state" '$plugin'
  else
    printf '[]\\n'
  fi
elif [ "$1" = plugin ] && { [ "$2" = install ] || [ "$2" = update ]; }; then
  catalog="$HOME/.local/share/claude/ponytail-marketplace/.claude-plugin/marketplace.json"
  version="$(jq -r '.plugins[0].source.ref | ltrimstr("v")' "$catalog")"
  enabled=true
  if [ "$2" = update ]; then
    enabled="$(jq -r '.enabled' "$state")"
  fi
  jq -n --arg version "$version" --argjson enabled "$enabled" \\
    '{id: "ponytail@ponytail", scope: "user", version: $version, enabled: $enabled}' > "$state"
  printf '%s\\n' "$2" >> "$actions"
elif [ "$1 $2" = 'plugin enable' ]; then
  jq '.enabled = true' "$state" > "$state.tmp"
  mv "$state.tmp" "$state"
  printf 'enable\\n' >> "$actions"
else
  exit 2
fi
"""
    )
    claude.chmod(0o755)
    return home


def sync(home: Path, *, http_status: int = 200) -> subprocess.CompletedProcess[str]:
    """Run the Puppet-managed plugin script with fake external commands."""
    env = os.environ.copy()
    env.update(HOME=str(home), FAKE_HTTP_STATUS=str(http_status))
    return subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True
    )


def catalog_ref(home: Path) -> str:
    """Read the Git ref pinned in the generated marketplace."""
    catalog = (
        home
        / ".local/share/codex/ponytail-marketplace/.agents/plugins/marketplace.json"
    )
    return json.loads(catalog.read_text())["plugins"][0]["source"]["ref"]


def claude_catalog_ref(home: Path) -> str:
    """Read the Git ref pinned in the generated Claude marketplace."""
    catalog = (
        home
        / ".local/share/claude/ponytail-marketplace/.claude-plugin/marketplace.json"
    )
    return json.loads(catalog.read_text())["plugins"][0]["source"]["ref"]


def test_fast_path_and_reenable(plugin_home: Path) -> None:
    """Use local state on repeated runs and repair a disabled installation."""
    (plugin_home / "releases-fixture.json").write_text(
        json.dumps([release("1.1.0", 1), release("1.0.0", 7)])
    )
    assert sync(plugin_home).returncode == 0
    assert catalog_ref(plugin_home) == "v1.0.0"
    assert claude_catalog_ref(plugin_home) == "v1.0.0"
    assert (plugin_home / "curl-count").read_text().strip() == "1"
    assert (plugin_home / "add-count").read_text().strip() == "1"
    assert (plugin_home / "claude-actions").read_text().splitlines() == [
        "marketplace add",
        "install",
    ]

    assert sync(plugin_home).returncode == 0
    assert (plugin_home / "curl-count").read_text().strip() == "1"
    assert (plugin_home / "add-count").read_text().strip() == "1"
    assert (plugin_home / "claude-actions").read_text().splitlines() == [
        "marketplace add",
        "install",
    ]

    state = plugin_home / "codex-state.json"
    state.write_text(
        json.dumps(
            {"pluginId": "ponytail@ponytail", "version": "1.0.0", "enabled": False}
        )
    )
    assert sync(plugin_home).returncode == 0
    assert (plugin_home / "curl-count").read_text().strip() == "1"
    assert (plugin_home / "add-count").read_text().strip() == "2"

    claude_state = plugin_home / "claude-state.json"
    claude_state.write_text(
        json.dumps(
            {
                "id": "ponytail@ponytail",
                "scope": "user",
                "version": "1.0.0",
                "enabled": False,
            }
        )
    )
    assert sync(plugin_home).returncode == 0
    assert (plugin_home / "claude-actions").read_text().splitlines()[-1] == "enable"

    state.unlink()
    assert sync(plugin_home).returncode == 0
    assert (plugin_home / "curl-count").read_text().strip() == "1"
    assert (plugin_home / "add-count").read_text().strip() == "3"

    claude_state.unlink()
    assert sync(plugin_home).returncode == 0
    assert (plugin_home / "claude-actions").read_text().splitlines()[-1] == "install"


def test_refresh_and_cached_fallback(plugin_home: Path) -> None:
    """Refresh only when due and retain the selected version on HTTP failure."""
    fixture = plugin_home / "releases-fixture.json"
    fixture.write_text(json.dumps([release("1.0.0", 7)]))
    assert sync(plugin_home).returncode == 0

    fixture.write_text(json.dumps([release("1.1.0", 4), release("1.0.0", 7)]))
    checked_at = plugin_home / ".cache/puppet/codex-ponytail/checked-at"
    checked_at.write_text("0\n")
    assert sync(plugin_home).returncode == 0
    assert catalog_ref(plugin_home) == "v1.1.0"
    assert claude_catalog_ref(plugin_home) == "v1.1.0"
    assert (plugin_home / "curl-count").read_text().strip() == "2"
    assert (plugin_home / "add-count").read_text().strip() == "2"
    assert (plugin_home / "claude-actions").read_text().splitlines()[-1] == "update"

    checked_at.write_text("0\n")
    assert sync(plugin_home, http_status=304).returncode == 0
    assert (plugin_home / "curl-count").read_text().strip() == "3"
    assert (plugin_home / "add-count").read_text().strip() == "2"

    checked_at.write_text("0\n")
    assert sync(plugin_home, http_status=503).returncode == 0
    assert (plugin_home / "curl-count").read_text().strip() == "4"
    assert (plugin_home / "add-count").read_text().strip() == "2"
    assert sync(plugin_home).returncode == 0
    assert (plugin_home / "curl-count").read_text().strip() == "4"


def test_cached_release_becomes_eligible_without_network(plugin_home: Path) -> None:
    """Reconsider cached release dates on every run without an HTTP check."""
    fixture = plugin_home / "releases-fixture.json"
    fixture.write_text(json.dumps([release("1.1.0", 1), release("1.0.0", 7)]))
    assert sync(plugin_home).returncode == 0
    assert catalog_ref(plugin_home) == "v1.0.0"

    cached = plugin_home / ".cache/puppet/codex-ponytail/releases.json"
    cached.write_text(json.dumps([release("1.1.0", 4), release("1.0.0", 7)]))
    assert sync(plugin_home).returncode == 0
    assert catalog_ref(plugin_home) == "v1.1.0"
    assert (plugin_home / "curl-count").read_text().strip() == "1"
    assert (plugin_home / "add-count").read_text().strip() == "2"


def test_initial_http_failure_is_throttled(plugin_home: Path) -> None:
    """Avoid repeated network attempts when no release cache exists yet."""
    assert sync(plugin_home, http_status=503).returncode == 1
    assert sync(plugin_home, http_status=503).returncode == 1
    assert (plugin_home / "curl-count").read_text().strip() == "1"
