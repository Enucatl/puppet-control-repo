"""Exercise cutover ordering and recovery without touching router services."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest

yaml = pytest.importorskip("yaml")
ROOT = Path(__file__).parents[1] / "provisioning"


@pytest.fixture(scope="module")
def cutover_scripts(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    """Render migration and active maintenance with production Ansible filters."""
    if not shutil.which("ansible-playbook"):
        pytest.skip("Use the provisioning environment for Ansible rendering")
    directory = tmp_path_factory.mktemp("cutover")
    tasks = []
    for name in ["cutover", "configure"]:
        tasks.append(
            {
                "ansible.builtin.template": {
                    "src": str(ROOT / f"templates/app_configs/technitium-{name}.sh.j2"),
                    "dest": str(directory / name),
                },
            }
        )
    play = [
        {
            "hosts": "localhost",
            "connection": "local",
            "gather_facts": False,
            "vars_files": [
                str(ROOT / "host_vars/router.yml"),
                str(ROOT / "vars/technitium.yml"),
                str(ROOT / "vars/technitium-legacy.yml"),
            ],
            "vars": {"ipv6_prefix": "2001:db8:1234"},
            "tasks": tasks,
        }
    ]
    path = directory / "render.yml"
    path.write_text(yaml.safe_dump(play))
    result = subprocess.run(
        [
            "ansible-playbook",
            "-i",
            "localhost,",
            str(path),
            "-e",
            '{"technitium_active": true}',
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return {name: (directory / name).read_text() for name in ["cutover", "configure"]}


def test_cutover_order_and_maintenance(cutover_scripts: dict[str, str]) -> None:
    """Require lease ownership checks before listeners and safe rollback ordering."""
    for script in cutover_scripts.values():
        subprocess.run(["bash", "-n"], input=script, text=True, check=True)
        for line in script.splitlines():
            if line.lstrip().startswith("token=$(api user/login"):
                assert line.rstrip().endswith("| jq -er .token)"), line
    maintenance = cutover_scripts["configure"]
    assert "dnsServerLocalEndPoints=127.0.0.1:53" in maintenance
    assert "api dhcp/scopes/" not in maintenance
    assert "type=FWD" not in maintenance
    assert '[[ -f "$data/cutover-active" ]]' in maintenance
    migration = cutover_scripts["cutover"]
    cutover, rollback = migration.split("\nrollback)\n", 1)
    steps = [
        "trap 'if [[ $BASHPID",
        "systemctl stop vyos-container-dnsmasq.service",
        '"$backup/dnsmasq-final.leases"',
        'python3 "$helper"',
        "    start_isolated",
        "api dhcp/scopes/set",
        "api settings/set 'dnsServerLocalEndPoints=127.0.0.1:53",
        '[[ "$observed" == "$expected" ]]',
        'touch "$data/cutover-active"',
        'configure_vyos "$backup/activate.commands"',
        "api dhcp/scopes/enable",
    ]
    offsets = [cutover.index(step) for step in steps]
    assert offsets == sorted(offsets)
    steps = [
        "systemctl stop vyos-container-technitium.service",
        "    start_isolated",
        "api dhcp/leases/list",
        'python3 "$helper"',
        "    stop_isolated",
        "podman run --rm --network none",
        'configure_vyos "$backup/remove-active.commands"',
        'configure_vyos "$backup/restore-containers.commands"',
        'mv "$data/cutover-active"',
    ]
    offsets = [rollback.index(step) for step in steps]
    assert offsets == sorted(offsets)


@pytest.mark.parametrize(
    "failure", ["lease_merge", "isolated_start", "isolated_ready", "login_substitution"]
)
@pytest.mark.parametrize("mode", ["cutover", "rehearse"])
def test_pre_activation_failure_resumes_dnsmasq(
    tmp_path: Path,
    cutover_scripts: dict[str, str],
    failure: str,
    mode: str,
) -> None:
    """Execute the real shell flow with failing helpers and mocked router commands."""
    config = tmp_path / "config"
    files = {
        "config.boot": "original router config",
        "containers/alloy/conf/config.alloy": "original Alloy config",
        "containers/adguard/conf/AdGuardHome.yaml": "original AdGuard config",
        "containers/dnsmasq/conf/dnsmasq.conf": "original dnsmasq config",
        "containers/dnsmasq/leases/dnsmasq.leases": "0 00:11:22:33:44:55 10.0.0.123 client *\n",
        "containers/technitium-candidate/admin-password": "test-password",
        "scripts/technitium-scopes.json": "[]",
        "scripts/technitium-hosts.json": "[]",
        "scripts/technitium-leases.py": "raise SystemExit(42)\n"
        if failure == "lease_merge"
        else "print('{}')\n",
    }
    for name, content in files.items():
        path = config / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    commands = tmp_path / "commands"
    commands.mkdir()
    events = tmp_path / "events"
    podman = commands / "podman"
    podman.write_text(f"""#!/bin/bash
printf '%s\\n' "$*" >> '{events}'
case "$1" in
inspect) echo 'saved container identity';;
run) {"exit 43" if failure == "isolated_start" else "exit 0"};;
exec)
    if [[ '{failure}' == login_substitution && "$*" == */api/status ]]; then
        echo '{{"status":"ok"}}'
    else
        exit 44
    fi;;
container) {"exit 0" if failure in ["isolated_ready", "login_substitution"] else "exit 1"};;
*) exit 0;;
esac
""")
    podman.chmod(0o755)
    systemctl = commands / "systemctl"
    systemctl.write_text(f'''#!/bin/sh\nprintf "%s\\n" "$*" >> "{events}"\n''')
    systemctl.chmod(0o755)
    wrapper = commands / "vyatta-op-cmd-wrapper"
    wrapper.write_text("#!/bin/sh\necho 'set container name technitium memory 512'\n")
    wrapper.chmod(0o755)
    script = cutover_scripts["cutover"].replace("/config/", str(config) + "/")
    script = script.replace(
        "/run/lock/technitium-cutover.lock", str(tmp_path / "cutover.lock")
    )
    script = script.replace("/opt/vyatta/bin/vyatta-op-cmd-wrapper", str(wrapper))
    script = script.replace("tar --directory=/ ", f"tar --directory={tmp_path} ")
    # PATH substitution affects only external router commands; shell control flow is intact.
    script = script.replace(
        "set -Eeuo pipefail", f"set -Eeuo pipefail\nexport PATH='{commands}':\"$PATH\""
    )
    result = subprocess.run(
        ["bash", "-s", "--", mode], input=script, text=True, capture_output=True
    )
    assert result.returncode != 0, result.stdout + result.stderr
    calls = events.read_text().splitlines()
    if mode == "cutover":
        assert "stop vyos-container-dnsmasq.service" in calls
        assert calls[-1] == "start vyos-container-dnsmasq.service", calls
        assert calls.count("start vyos-container-dnsmasq.service") == 1, calls
    else:
        assert not any("vyos-container-" in call for call in calls), calls
    assert (
        "stop vyos-container-adguard.service vyos-container-technitium.service"
        not in calls
    )
    assert not (config / "containers/technitium-candidate/cutover-active").exists()
    if failure == "lease_merge":
        assert not any(call.startswith("run ") for call in calls)
    if failure in ["isolated_ready", "login_substitution"]:
        assert "stop --time 30 technitium-migration" in calls
        assert "rm technitium-migration" in calls


@pytest.mark.parametrize("delayed", [False, True])
def test_scope_readiness_real_api(
    cutover_scripts: dict[str, str], tmp_path: Path, delayed: bool
) -> None:
    """Run the migration readiness guard against loaded or briefly missing scopes."""
    base = os.environ.get("TECHNITIUM_API_URL")
    if not base:
        pytest.skip("Requires the isolated API with candidate scopes configured")
    (tmp_path / "admin-password").write_text("test-password")
    helper = (ROOT / "templates/app_configs/technitium-api.sh.j2").read_text()
    wait = (
        "wait_scopes() {"
        + cutover_scripts["cutover"]
        .split("wait_scopes() {", 1)[1]
        .split("\nconfigure_vyos()", 1)[0]
    )
    script = f"""set -euo pipefail
container=test
data='{tmp_path}'
podman() {{ shift 2; "$@"; }}
""" + helper.replace("http://127.0.0.1:5380/api/", base)
    if delayed:
        script = script.replace("api() {", "real_api() {").replace(
            "token=$(api user/login", "token=$(real_api user/login"
        )
        script += f"""
api() {{
    if [[ $1 == dhcp/scopes/list ]]; then
        count=$(cat '{tmp_path}/calls' 2>/dev/null || echo 0)
        echo $((count + 1)) > '{tmp_path}/calls'
        if ((count < 2)); then
            echo '{{"status":"ok","response":{{"scopes":[]}}}}'
            return
        fi
    fi
    real_api "$@"
}}
sleep() {{ :; }}
"""
    script += "\n" + wait + "\nwait_scopes\napi user/logout >/dev/null\n"
    result = subprocess.run(["bash"], input=script, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    if delayed:
        assert (tmp_path / "calls").read_text().strip() == "3"


def test_reservation_substitution_failure_recovers_once(
    cutover_scripts: dict[str, str], tmp_path: Path
) -> None:
    """Stop at the first missing scope and run cleanup only in the parent shell."""
    migration = cutover_scripts["cutover"]
    reservations = (
        "reservations() {"
        + migration.split("reservations() {", 1)[1].split(
            "    expected=$(reservations", 1
        )[0]
    )
    trap = next(
        line for line in migration.splitlines() if line.lstrip().startswith("trap '")
    )
    events = tmp_path / "events"
    expected_assignment = next(
        line
        for line in migration.splitlines()
        if line.lstrip().startswith("expected=$(reservations")
    )
    script = f"""set -Eeuo pipefail
migration_pid=$BASHPID
backup='{tmp_path}'
mode=cutover
podman() {{ echo "podman $*" >> '{events}'; }}
systemctl() {{ echo "systemctl $*" >> '{events}'; }}
api() {{ echo "api $*" >> '{events}'; return 42; }}
{trap}
{reservations}
{expected_assignment}
"""
    result = subprocess.run(["bash"], input=script, text=True, capture_output=True)
    assert result.returncode != 0, result.stdout + result.stderr
    calls = events.read_text().splitlines()
    assert len([call for call in calls if call.startswith("api ")]) == 1, calls
    assert calls.count("systemctl start vyos-container-dnsmasq.service") == 1, calls
