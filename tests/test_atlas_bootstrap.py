"""Run Atlas bootstrap against isolated files, real Git and harmless host commands."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import shlex
import subprocess
import re

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "proxmox/atlas-bootstrap.sh"
FINGERPRINT = "9DC858229FC7DD38854AE2D88D81803C0EBFCD88"
SYSBOX_FIXTURE = b"fixture Sysbox package\n"


def git(directory: Path, *arguments: str) -> str:
    """Run real Git against a disposable repository."""
    return subprocess.check_output(
        ["git", "-C", str(directory), *arguments], text=True, stderr=subprocess.PIPE
    ).strip()


@pytest.fixture
def bootstrap(tmp_path: Path) -> Path:
    """Copy the script into a fake host with local Git origins and command doubles."""
    for directory in ("etc/apt/sources.list.d", "bin", "home", "upstream"):
        (tmp_path / directory).mkdir(parents=True)
    (tmp_path / "etc/os-release").write_text("ID=ubuntu\nVERSION_ID=26.04\n")
    for mapping in ("subuid", "subgid"):
        (tmp_path / "etc" / mapping).write_text("user_l:100000:65536\n")
    (tmp_path / "hostname").write_text("atlas.home.arpa\n")
    (tmp_path / "architecture").write_text("amd64\n")
    (tmp_path / "failure").write_text("")
    (tmp_path / "fingerprint").write_text(FINGERPRINT + "\n")
    revisions = {}
    for name in ("puppet-control-repo", "docker-compose-security-baseline"):
        repository = tmp_path / "upstream" / name
        repository.mkdir()
        git(repository, "init", "-q")
        (repository / "fixture").write_text(name)
        git(repository, "add", ".")
        git(
            repository,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "Fixture",
        )
        revisions[name] = git(repository, "rev-parse", "HEAD")
    (tmp_path / "revision").write_text(revisions["puppet-control-repo"])
    source = SCRIPT.read_text()
    for name in revisions:
        source = source.replace(
            f"git@github.com:Enucatl/{name}.git", str(tmp_path / "upstream" / name)
        )
    source = source.replace(
        "41c57257d33d471e2ae7cc7264938944c67434fd",
        revisions["docker-compose-security-baseline"],
    )
    source = source.replace(
        "9d6d5484f980d0a17f86c492c1262015c2afb66280bdb97215b79fde6a0261c5",
        hashlib.sha256(SYSBOX_FIXTURE).hexdigest(),
    )
    for prefix in ("/etc/", "/opt/", "/var/"):
        source = source.replace(prefix, str(tmp_path) + prefix)
    (tmp_path / "bootstrap.sh").write_text(source)
    command = (
        f"#!/bin/bash\nset -eu\nroot={shlex.quote(str(tmp_path))}\n"
        + r"""
name=${0##*/}
printf '%s %s\n' "$name" "$*" >> "$root/commands"
failure=$(cat "$root/failure")
case "$name" in
  id)
    if [[ ${2:-} == dockremap ]]; then test -f "$root/dockremap-user"; else echo 0; fi
    ;;
  getent) test -f "$root/docker-group" ;;
  groupadd) touch "$root/docker-group" ;;
  useradd) touch "$root/dockremap-user" ;;
  usermod)
    case "$1" in
      --del-subuids|--del-subgids) echo 'user_l is currently used by a process' >&2; exit 8 ;;
      --add-subuids) printf 'dockremap:100000:65536\n' >> "$root/etc/subuid" ;;
      --add-subgids) printf 'dockremap:100000:65536\n' >> "$root/etc/subgid" ;;
      --append) [[ "$*" == '--append --groups docker user_l' ]] ;;
      *) exit 99 ;;
    esac
    ;;
  sed) exec /usr/bin/sed "$@" ;;
  runuser)
    [[ $1 == -u && $2 == user_l && $3 == -- ]]
    shift 3
    exec "$@"
    ;;
  chown) [[ $1 == -R && $2 == user_l:docker && $3 == "$root/opt/docker/"* ]] ;;
  install)
    arguments=()
    while (( $# )); do
      case "$1" in
        -o|-g) shift 2 ;;
        *) arguments+=("$1"); shift ;;
      esac
    done
    exec /usr/bin/install "${arguments[@]}"
    ;;
  hostname) cat "$root/hostname" ;;
  dpkg) cat "$root/architecture" ;;
  apt-get)
    if [[ $failure == packages && "$*" == *docker-ce=* ]]; then exit 42; fi
    if [[ $failure == sysbox-package && "$*" == *sysbox-ce.deb* ]]; then exit 42; fi
    ;;
  curl)
    if [[ "$*" == *sysbox-ce_0.7.1.linux_amd64.deb* && $failure != checksum ]]; then
      printf 'fixture Sysbox package\n' > "${@: -1}"
    else
      printf 'fixture key\n' > "${@: -1}"
    fi
    ;;
  gpg)
    printf 'pub:::::::::\nfpr:::::::::%s:\n' "$(cat "$root/fingerprint")"
    ;;
  docker)
    if [[ " $* " == *' info '* ]]; then
      [[ $1 == --host && $2 == "unix://$root/var/run/docker.sock" ]]
      shift 2
      [[ $1 == info ]]
      runtime='{"sysbox-runc":{}}'
      [[ $failure != runtime ]] || runtime='{}'
      printf '{"DockerRootDir":"%s/var/lib/docker/100000.100000","SecurityOptions":["name=userns"],"ServerVersion":"29.9.1","Driver":"overlayfs","Runtimes":%s}\n' "$root" "$runtime"
    elif [[ $1 == --host ]]; then
      [[ $2 == "unix://$root/var/run/docker.sock" ]]
      shift 2
      [[ $failure != inventory ]] || exit 42
      case "$*" in
        'ps -aq') resource=containers ;;
        'volume ls -q') resource=volumes ;;
        'network ls --filter type=custom --quiet') resource=networks ;;
        *) exit 99 ;;
      esac
      if [[ $failure == "$resource" ]]; then printf 'existing-%s\n' "$resource"; fi
    elif [[ "$*" == *'config --quiet'* ]]; then
      [[ -z ${VAULT_TOKEN:-} && -z ${COMPOSE_FILE:-} && -z ${DOCKER_HOST:-} && -z ${DOCKER_CONTEXT:-} ]]
      [[ $DOCKER_DOMAIN == atlas.home.arpa && "$*" == *'--env-file /dev/null'* ]]
      [[ $failure != compose ]] || exit 42
    fi
    ;;
  dpkg-query)
    if [[ "$*" == *'${Version}'* ]]; then
      [[ $failure != sysbox-version ]] && printf '0.7.1.linux' || printf '0.6.7.linux'
    else
      printf 'docker-ce\tfixture-version\nsysbox-ce\t0.7.1.linux\n'
    fi
    ;;
  systemctl)
    if [[ $failure == sysbox-service && $1 == is-active ]]; then exit 42; fi
    ;;
  *) exit 99 ;;
esac
"""
    )
    for name in (
        "id",
        "hostname",
        "dpkg",
        "apt-get",
        "curl",
        "gpg",
        "docker",
        "dpkg-query",
        "systemctl",
        "getent",
        "groupadd",
        "useradd",
        "usermod",
        "runuser",
        "chown",
        "install",
        "sed",
    ):
        binary = tmp_path / "bin" / name
        binary.write_text(command)
        binary.chmod(0o755)
    return tmp_path


def run_bootstrap(
    root: Path, revision: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Execute the copied script with network/package operations replaced."""
    return subprocess.run(
        [
            "bash",
            str(root / "bootstrap.sh"),
            revision if revision is not None else (root / "revision").read_text(),
        ],
        env={
            "PATH": f"{root / 'bin'}:{os.environ['PATH']}",
            "HOME": str(root / "home"),
            "VAULT_TOKEN": "must-not-reach-compose",
            "COMPOSE_FILE": "/unrelated/compose.yml",
            "DOCKER_HOST": "tcp://old-host:2375",
            "DOCKER_CONTEXT": "old-host",
        },
        text=True,
        capture_output=True,
        timeout=15,
    )


def test_fresh_and_repeated_bootstrap(bootstrap: Path) -> None:
    """Install once, retain checkouts/settings on rerun and validate sanitized Compose."""
    for _ in range(2):
        result = run_bootstrap(bootstrap)
        assert result.returncode == 0, result.stderr
        checkout = bootstrap / "opt/docker/puppet-control-repo"
        assert (
            git(checkout, "rev-parse", "HEAD") == (bootstrap / "revision").read_text()
        )
        assert git(checkout, "status", "--porcelain") == ""
        evidence = json.loads(
            (
                bootstrap / "var/log/openvox-provisioning/atlas-bootstrap.json"
            ).read_text()
        )
        assert evidence["control_revision"] == git(checkout, "rev-parse", "HEAD")
        assert evidence["userns_base"] == 100000
    assert len(list((bootstrap / "etc/apt/sources.list.d").iterdir())) == 1
    assert (
        json.loads((bootstrap / "etc/docker/daemon.json").read_text())["userns-remap"]
        == "default"
    )
    for mapping in ("subuid", "subgid"):
        assert (bootstrap / "etc" / mapping).read_text() == "dockremap:100000:65536\n"
    assert (bootstrap / "opt/docker").stat().st_mode & 0o7777 == 0o2775
    calls = (bootstrap / "commands").read_text()
    assert calls.count("usermod --append --groups docker user_l\n") == 2
    assert calls.count("useradd --system") == 1
    assert calls.count("usermod --add-subuids") == 1
    assert calls.count("usermod --add-subgids") == 1
    for name in ("puppet-control-repo", "compose-security-baseline"):
        assert (
            calls.count(f"chown -R user_l:docker {bootstrap}/opt/docker/{name}\n") == 2
        )
    assert calls.count("runuser -u user_l -- git clone") == 2
    assert calls.count("systemctl is-active --quiet sysbox sysbox-mgr sysbox-fs\n") == 2
    assert calls.count("systemctl enable --now docker\n") == 2
    assert "config --quiet" in calls
    assert "compose up" not in calls


@pytest.mark.parametrize(
    ("file", "value"),
    [
        ("hostname", "docker.home.arpa"),
        ("etc/os-release", "ID=ubuntu\nVERSION_ID=24.04\n"),
        ("architecture", "arm64"),
        ("etc/subuid", "user_l:165536:65536\n"),
        ("etc/subgid", "user_l:100000:65536\nuser_l:165536:65536\n"),
        ("etc/subuid", "other:110000:65536\n"),
        ("etc/subgid", "dockremap:200000:65536\n"),
    ],
)
def test_wrong_target_refused_before_packages(
    bootstrap: Path, file: str, value: str
) -> None:
    """Reject hosts or ownership mappings outside the declared Atlas prerequisites."""
    (bootstrap / file).write_text(value)
    assert run_bootstrap(bootstrap).returncode != 0
    assert "apt-get" not in (bootstrap / "commands").read_text()
    assert not (bootstrap / "opt/docker").exists()


@pytest.mark.parametrize("revision", ["dev", "-bad", "a" * 39, "f" * 40])
def test_invalid_or_missing_commit_refused(bootstrap: Path, revision: str) -> None:
    """Require a real full commit and never enable Docker after failed fetches."""
    assert run_bootstrap(bootstrap, revision).returncode != 0
    assert not (bootstrap / "etc/docker/daemon.json").exists()


@pytest.mark.parametrize("state", ["different-settings", "unconfigured-state"])
def test_existing_docker_state_is_preserved(bootstrap: Path, state: str) -> None:
    """Do not remap an existing daemon or silently adopt its stored containers."""
    daemon = bootstrap / "etc/docker/daemon.json"
    if state == "different-settings":
        daemon.parent.mkdir()
        daemon.write_text('{"userns-remap":"default"}\n')
    else:
        (bootstrap / "var/lib/docker").mkdir(parents=True)
    assert run_bootstrap(bootstrap).returncode != 0
    assert "systemctl" not in (bootstrap / "commands").read_text()
    if daemon.exists():
        assert json.loads(daemon.read_text()) == {"userns-remap": "default"}


@pytest.mark.parametrize(
    "occupied", [None, "containers", "volumes", "networks", "inventory"]
)
def test_previous_bootstrap_upgrade_requires_empty_daemon(
    bootstrap: Path, occupied: str | None
) -> None:
    """Upgrade only the empty known daemon, stopping it before any settings transfer."""
    daemon = bootstrap / "etc/docker/daemon.json"
    daemon.parent.mkdir()
    previous = {
        "userns-remap": "user_l",
        "userland-proxy": False,
        "log-driver": "json-file",
        "log-opts": {"max-size": "10m", "max-file": "3"},
    }
    daemon.write_text(json.dumps(previous))
    (bootstrap / "var/lib/docker").mkdir(parents=True)
    (bootstrap / "failure").write_text(occupied or "")
    result = run_bootstrap(bootstrap)
    calls = (bootstrap / "commands").read_text().splitlines()
    stop = "systemctl stop docker.service docker.socket"
    if occupied:
        assert result.returncode != 0
        assert json.loads(daemon.read_text()) == previous
        assert stop not in calls
        assert not any(call.startswith("usermod ") for call in calls)
        for mapping in ("subuid", "subgid"):
            assert (bootstrap / "etc" / mapping).read_text() == "user_l:100000:65536\n"
        return
    assert result.returncode == 0, result.stderr
    first_daemon_write = next(
        index
        for index, call in enumerate(calls)
        if call.startswith("install -m 0644 ") and call.endswith(str(daemon))
    )
    transfer = next(
        index
        for index, call in enumerate(calls)
        if call.startswith("sed -i /^user_l:100000:65536$/d ")
    )
    add_mapping = calls.index("usermod --add-subuids 100000-165535 dockremap")
    assert calls.index(stop) < first_daemon_write < transfer < add_mapping
    assert json.loads(daemon.read_text())["userns-remap"] == "default"
    for mapping in ("subuid", "subgid"):
        assert (bootstrap / "etc" / mapping).read_text() == "dockremap:100000:65536\n"
    result = run_bootstrap(bootstrap)
    assert result.returncode == 0, result.stderr
    assert (bootstrap / "commands").read_text().splitlines().count(stop) == 1


@pytest.mark.parametrize("problem", ["dirty", "wrong-origin"])
def test_modified_checkout_is_preserved(bootstrap: Path, problem: str) -> None:
    """Refuse unexpected existing repositories rather than resetting user work."""
    assert run_bootstrap(bootstrap).returncode == 0
    checkout = bootstrap / "opt/docker/puppet-control-repo"
    if problem == "dirty":
        (checkout / "fixture").write_text("operator change")
    else:
        git(checkout, "remote", "set-url", "origin", "/unrelated/repository")
    owner_changes = (bootstrap / "commands").read_text().count("chown -R")
    result = run_bootstrap(bootstrap)
    assert result.returncode != 0
    assert "Refusing to overwrite" in result.stderr
    assert (bootstrap / "commands").read_text().count("chown -R") == owner_changes
    if problem == "dirty":
        assert (checkout / "fixture").read_text() == "operator change"
    else:
        assert git(checkout, "remote", "get-url", "origin") == "/unrelated/repository"


@pytest.mark.parametrize("uid_mapping", ["", "dockremap:100000:65536\n"])
def test_interrupted_mapping_transfer_can_resume(
    bootstrap: Path, uid_mapping: str
) -> None:
    """Resume after range removal or UID transfer without overlapping other users."""
    (bootstrap / "dockremap-user").touch()
    other = "other:165536:65536\n"
    (bootstrap / "etc/subuid").write_text(uid_mapping + other)
    (bootstrap / "etc/subgid").write_text("user_l:100000:65536\n" + other)
    result = run_bootstrap(bootstrap)
    assert result.returncode == 0, result.stderr
    for mapping in ("subuid", "subgid"):
        lines = (bootstrap / "etc" / mapping).read_text().splitlines()
        assert sorted(lines) == ["dockremap:100000:65536", "other:165536:65536"]
    assert "useradd" not in (bootstrap / "commands").read_text()


@pytest.mark.parametrize(
    "failure",
    [
        "packages",
        "fingerprint",
        "compose",
        "checksum",
        "sysbox-package",
        "sysbox-version",
        "sysbox-service",
        "runtime",
    ],
)
def test_failed_setup_never_records_success(bootstrap: Path, failure: str) -> None:
    """Propagate package, trust and Compose failures without success evidence."""
    (bootstrap / "failure").write_text(failure)
    if failure == "fingerprint":
        (bootstrap / "fingerprint").write_text("A" * 40)
    assert run_bootstrap(bootstrap).returncode != 0
    assert not (
        bootstrap / "var/log/openvox-provisioning/atlas-bootstrap.json"
    ).exists()
    calls = (bootstrap / "commands").read_text()
    if failure not in ("compose", "runtime"):
        assert "systemctl enable --now docker" not in calls
    if failure == "checksum":
        assert not any(
            line.startswith("apt-get install") and "sysbox-ce.deb" in line
            for line in calls.splitlines()
        )


def test_daemon_networks_do_not_overlap_existing_routes(bootstrap: Path) -> None:
    """Reuse legacy IPv4 settings while reserving separate Atlas IPv6 allocations."""
    result = run_bootstrap(bootstrap)
    assert result.returncode == 0, result.stderr
    daemon = json.loads((bootstrap / "etc/docker/daemon.json").read_text())
    pools = [
        ipaddress.ip_network(pool["base"], strict=False)
        for pool in daemon["default-address-pools"]
    ]
    bridges = [
        ipaddress.ip_interface(daemon["bip"]).network,
        ipaddress.ip_network(daemon["fixed-cidr-v6"]),
    ]
    for pool in pools:
        for bridge in bridges:
            if pool.version == bridge.version == 6:
                assert not pool.overlaps(bridge)
    router = SCRIPT.parents[1] / "provisioning/templates/partials/nat_routing.j2"
    suffixes = re.findall(r"route6 \{\{ ipv6_prefix \}\}:(\S+)", router.read_text())
    assert suffixes
    for suffix in suffixes:
        allocated = ipaddress.ip_network("2a02:168:6278:" + suffix)
        assert all(
            not allocated.overlaps(network)
            for network in pools + bridges
            if network.version == 6
        )
    assert daemon["default-address-pools"][0] == {
        "base": "172.17.0.0/12",
        "size": 20,
    }
    assert daemon["bip"] == "172.20.0.1/16"
    assert pools[0] == ipaddress.ip_network("172.16.0.0/12")
    assert bridges[0].subnet_of(pools[0])
    assert daemon["experimental"] is True
    assert daemon["ipv6"] is True
    assert daemon["userland-proxy"] is False
    assert daemon["log-driver"] == "json-file"
    assert daemon["log-opts"] == {"max-size": "10m", "max-file": "3"}
    assert daemon["runtimes"]["sysbox-runc"]["path"] == "/usr/bin/sysbox-runc"
