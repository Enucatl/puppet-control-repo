"""Protect Atlas from disposable rebuilds before authority initialization."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from urllib.parse import quote

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "proxmox/atlas-promote.sh"
OWNER = "openvox-provisioning:docker:atlas.home.arpa"
MARKER = "openvox-authority:atlas.home.arpa"
CONFIG = (
    f"name: atlas\ndescription: {OWNER}\nnet0: virtio=BC:24:11:4E:A4:9E,bridge=vmbr0\n"
)


@pytest.fixture
def promotion(tmp_path: Path) -> Path:
    """Mock Proxmox and root ownership while executing the guest's file writes."""
    binary = tmp_path / "bin"
    binary.mkdir()
    guest = tmp_path / "guest"
    guest.mkdir()
    (tmp_path / "promote.sh").write_text(
        SOURCE.read_text().replace("/etc/openvox-infra", str(guest / "openvox-infra"))
    )
    (tmp_path / "config").write_text(CONFIG)
    scripts = {
        "id": '#!/bin/sh\nprintf "%s\\n" "${TEST_UID:-0}"\n',
        "chown": '#!/bin/sh\nprintf "chown %s\\n" "$*" >> "$TEST_ROOT/ownership"\n',
        "install": """#!/bin/bash
set -eu
printf 'install %s\n' "$*" >> "$TEST_ROOT/ownership"
args=()
while (( $# )); do
    case "$1" in -o|-g) shift 2;; *) args+=("$1"); shift;; esac
done
exec /usr/bin/install "${args[@]}"
""",
        "qm": """#!/bin/bash
set -eu
printf '%s\n' "$*" >> "$TEST_ROOT/commands"
case "$1" in
config)
    [[ "$*" == 'config 210' ]]
    cat "$TEST_ROOT/config";;
set)
    [[ "$*" == 'set 210 --description openvox-authority:atlas.home.arpa' ]]
    [[ ${FAIL_SET:-0} == 0 ]] || exit 1
    sed -i 's/^description:.*/description: openvox-authority:atlas.home.arpa/' "$TEST_ROOT/config";;
guest)
    [[ "$1 $2 $3 $4" == 'guest exec 210 --' ]]
    [[ ${FAIL_QM:-0} == 0 ]] || exit 1
    if [[ -n ${GUEST_RESULT:-} ]]; then printf '%s\n' "$GUEST_RESULT"; exit; fi
    shift 4
    "$@"
    printf '{"exited":1,"exitcode":0}\n';;
*) exit 99;;
esac
""",
    }
    for name, source in scripts.items():
        path = binary / name
        path.write_text(source)
        path.chmod(0o755)
    return tmp_path


def run_promotion(
    root: Path, *arguments: str, **environment: str
) -> subprocess.CompletedProcess[str]:
    """Run only copied code against the fake hypervisor and temporary guest."""
    return subprocess.run(
        ["bash", str(root / "promote.sh"), *arguments],
        env={
            **os.environ,
            "PATH": f"{root / 'bin'}:{os.environ['PATH']}",
            "TEST_ROOT": str(root),
            **environment,
        },
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.parametrize("encoded", [False, True])
def test_promote_and_repeat_preserves_authoritative_identity(
    promotion: Path, encoded: bool
) -> None:
    """Both Proxmox description forms promote safely and allow a repeat run."""
    if encoded:
        (promotion / "config").write_text(CONFIG.replace(OWNER, quote(OWNER, safe="")))
    for _ in range(2):
        result = run_promotion(promotion)
        assert result.returncode == 0, result.stderr
        assert OWNER not in (promotion / "config").read_text()
        sentinel = promotion / "guest/openvox-infra/promoted"
        assert sentinel.read_text() == MARKER + "\n"
        assert sentinel.stat().st_mode & 0o777 == 0o644
        assert sentinel.parent.stat().st_mode & 0o777 == 0o755
        ownership = (promotion / "ownership").read_text()
        assert "install -d -o root -g root -m 0755 " in ownership
        assert "chown root:root " in ownership
        if encoded:
            (promotion / "config").write_text(
                (promotion / "config")
                .read_text()
                .replace(MARKER, quote(MARKER, safe=""))
            )
    commands = (promotion / "commands").read_text()
    assert commands.index("set 210 --description") < commands.index("guest exec 210")


@pytest.mark.parametrize(
    "config",
    [
        CONFIG.replace("name: atlas", "name: other"),
        CONFIG.replace("BC:24:11:4E:A4:9E", "BC:24:11:4E:A4:9F"),
        CONFIG.replace(OWNER, "unrelated-owner"),
        CONFIG.replace(OWNER, OWNER + " extra"),
        CONFIG + "template: 1\n",
    ],
)
def test_refuses_unrelated_target_before_mutation(promotion: Path, config: str) -> None:
    """Ownership, identity and guest type must all match before promotion."""
    (promotion / "config").write_text(config)
    assert run_promotion(promotion).returncode != 0
    assert (promotion / "commands").read_text() == "config 210\n"


@pytest.mark.parametrize(
    "environment",
    [
        {"GUEST_RESULT": json.dumps({"exited": 1, "exitcode": 1})},
        {"GUEST_RESULT": json.dumps({"pid": 1234})},
        {"GUEST_RESULT": json.dumps({"exited": 0, "exitcode": 0})},
        {"GUEST_RESULT": "invalid json"},
        {"FAIL_QM": "1"},
    ],
)
def test_guest_failure_keeps_rebuild_protection_and_allows_retry(
    promotion: Path, environment: dict[str, str]
) -> None:
    """A pending or failed guest command cannot produce false promotion success."""
    result = run_promotion(promotion, **environment)
    assert result.returncode != 0
    assert "promoted to an authoritative host" not in result.stdout
    assert f"description: {MARKER}\n" in (promotion / "config").read_text()
    assert not (promotion / "guest/openvox-infra/promoted").exists()
    assert run_promotion(promotion).returncode == 0


def test_failed_protection_never_writes_guest_sentinel(promotion: Path) -> None:
    """Do not mark the guest promoted unless Proxmox rebuild protection succeeded."""
    assert run_promotion(promotion, FAIL_SET="1").returncode != 0
    assert "guest exec" not in (promotion / "commands").read_text()
    assert (promotion / "config").read_text() == CONFIG


@pytest.mark.parametrize("arguments,uid", [(("211",), "0"), ((), "1000")])
def test_fixed_target_and_root_required(
    promotion: Path, arguments: tuple[str, ...], uid: str
) -> None:
    """Reject a different VM request or unprivileged caller before invoking qm."""
    assert run_promotion(promotion, *arguments, TEST_UID=uid).returncode != 0
    assert not (promotion / "commands").exists()
