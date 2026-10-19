"""Back up isolated authority fixtures without Docker or live host mutations."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import tarfile

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "proxmox/atlas-infra-backup.sh"
IMAGES = ["sha256:" + value * 64 for value in "abc"]
VAULT = "hashicorp/vault@sha256:" + "d" * 64
VOLUMES = [
    "infra_openvox_" + name
    for name in ("ssl", "ca", "code", "data", "gems", "git", "r10k_cache", "trust")
] + ["infra_vault_data", "infra_vault_logs", "infra_certificates", "infra_vault_unseal"]


def git(repository: Path, *arguments: str) -> str:
    """Run real Git in temporary backup repositories."""
    return subprocess.check_output(
        ["git", "-C", str(repository), *arguments], text=True, stderr=subprocess.PIPE
    ).strip()


@pytest.fixture
def authority(tmp_path: Path) -> Path:
    """Build private state, real repositories and harmless recording host tools."""
    files = {
        "etc/openvox-infra/promoted": "openvox-authority:atlas.home.arpa\n",
        "etc/docker/daemon.json": '{"userns-remap":"default"}\n',
        "etc/subuid": "dockremap:100000:65536\n",
        "etc/subgid": "dockremap:100000:65536\n",
        "root/openvox-infra/root-token": "ROOT-TOKEN-CANARY\n",
        "root/openvox-infra/ready.json": json.dumps(
            {"root_sha256": "e" * 64, "control_commit": "f" * 40}
        ),
        "uid": "0",
        "hostname": "atlas.home.arpa",
        "failure": "",
    }
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    for name in VOLUMES:
        path = tmp_path / "volumes" / name
        path.mkdir(parents=True)
        (path / "state").write_text("PRIVATE-VOLUME-CANARY " + name)
    for name in ("puppet-control-repo", "compose-security-baseline"):
        repository = tmp_path / "opt/docker" / name
        repository.mkdir(parents=True)
        git(repository, "init", "-q", "--initial-branch=production")
        (repository / "configuration").write_text(name)
        git(repository, "add", ".")
        git(
            repository,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "Public configuration",
        )
    bare = tmp_path / "opt/git/openvox/control.git"
    bare.parent.mkdir(parents=True)
    git(
        tmp_path,
        "clone",
        "-q",
        "--bare",
        str(tmp_path / "opt/docker/puppet-control-repo"),
        str(bare),
    )
    bins = tmp_path / "bin"
    bins.mkdir()
    (tmp_path / "run/lock").mkdir(parents=True)
    double = (
        "#!/bin/bash\nset -eu\nroot="
        + shlex.quote(str(tmp_path))
        + "\n"
        + r"""
name=${0##*/}
printf '%s %s\n' "$name" "$*" >> "$root/commands"
failure=$(cat "$root/failure")
case "$name" in
    id) cat "$root/uid" ;;
    hostname) cat "$root/hostname" ;;
    flock)
        [[ "$*" == '-w 300 9' && $failure != lock ]] || exit 42
        exec /usr/bin/flock "$@"
        ;;
    tar)
        [[ $failure != credentials || "$*" != *credentials.tar.gz* ]] || exit 42
        arguments=()
        previous=''
        for argument in "$@"; do
            if [[ $previous == -C && $argument == / ]]; then argument=$root; fi
            arguments+=("$argument")
            previous=$argument
        done
        exec /usr/bin/tar "${arguments[@]}"
        ;;
    docker)
        [[ $1 == --host && $2 == unix:///var/run/docker.sock ]]
        shift 2
        case "$1 $2" in
            'volume inspect')
                volume=${@: -1}
                [[ $failure != missing-volume ]] || exit 42
                [[ $volume == infra_* && -d $root/volumes/$volume ]]
                if [[ $failure == unmanaged-volume ]]; then echo other; else echo atlas; fi
                ;;
            'inspect --format')
                running=true
                [[ $failure != stopped ]] || running=false
                printf 'sha256:%064d %s\n' 0 "$running" | tr 0 a
                printf 'sha256:%064d true\n' 0 | tr 0 b
                printf 'sha256:%064d true\n' 0 | tr 0 c
                ;;
            'image inspect')
                [[ ${@: -1} == hashicorp/vault@sha256:* ]]
                if [[ $failure == image ]]; then printf 'sha256:%064d\n' 0 | tr 0 f
                else printf 'sha256:%064d\n' 0 | tr 0 b; fi
                ;;
            'image save')
                [[ ! -f $root/services-stopped ]]
                [[ $failure != images ]] || exit 42
                printf 'OFFLINE-IMAGES\n'
                ;;
            'compose --env-file')
                [[ -z ${VAULT_TOKEN:-} && -z ${COMPOSE_FILE:-} && -z ${DOCKER_CONTEXT:-} ]]
                [[ $DOCKER_DOMAIN == home.arpa && "$*" == *'--env-file /dev/null -p infra'* ]]
                [[ "$*" == *'docker/docker-compose.yml -f '* && "$*" == *'docker/openvox/infra-vault.override.yaml '* ]]
                case "$*" in
                    *' config --format json')
                        printf '{"services":{"vault":{"image":"hashicorp/vault@sha256:%064d"}}}\n' 0 | tr 0 d
                        ;;
                    *' stop openvox vault-unsealer vault')
                        touch "$root/services-stopped"
                        [[ $failure != stop ]] || exit 42
                        ;;
                    *' start vault vault-unsealer openvox')
                        [[ $failure != restore ]] || exit 42
                        rm -f "$root/services-stopped"
                        ;;
                    *) exit 99 ;;
                esac
                ;;
            'run --rm')
                [[ -f $root/services-stopped ]]
                [[ "$*" == *'--network none --read-only --user 0:0'* ]]
                [[ "$*" == *'--cap-drop ALL --cap-add DAC_READ_SEARCH'* ]]
                [[ "$*" == *'--numeric-owner --acls --xattrs -czf - -C /volume .'* ]]
                volume=''
                for argument in "$@"; do
                    if [[ $argument =~ ^type=volume,src=(infra_[a-z0-9_]+),dst=/volume,readonly$ ]]; then
                        volume=${BASH_REMATCH[1]}
                    fi
                done
                [[ -n $volume ]]
                [[ $failure != archive ]] || exit 42
                exec /usr/bin/tar --numeric-owner --owner=999 --group=999 -czf - -C "$root/volumes/$volume" .
                ;;
            *) exit 99 ;;
        esac
        ;;
    *) exit 99 ;;
esac
"""
    )
    for name in ("id", "hostname", "docker", "tar", "flock"):
        path = bins / name
        path.write_text(double)
        path.chmod(0o755)
    source = SCRIPT.read_text()
    for prefix in ("/scratch/vault", "/etc/", "/root", "/opt/", "/run/lock/"):
        source = source.replace(prefix, str(tmp_path) + prefix)
    (tmp_path / "backup.sh").write_text(source)
    return tmp_path


def backup(root: Path, output: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run the isolated script with deliberately unsafe ambient Compose settings."""
    return subprocess.run(
        [
            "bash",
            str(root / "backup.sh"),
            output if output is not None else str(root / "scratch/vault/snapshot"),
        ],
        env={
            **os.environ,
            "PATH": f"{root / 'bin'}:{os.environ['PATH']}",
            "VAULT_TOKEN": "MUST-NOT-REACH-COMPOSE",
            "COMPOSE_FILE": "/unrelated/compose.yml",
            "DOCKER_CONTEXT": "wrong-host",
        },
        text=True,
        capture_output=True,
        timeout=15,
    )


def test_backup_preserves_private_state_numeric_ids_and_exact_images(
    authority: Path,
) -> None:
    """Produce verifiable offline archives while restarting only declared services."""
    result = backup(authority)
    assert result.returncode == 0, result.stderr
    output = authority / "scratch/vault/snapshot"
    assert output.stat().st_mode & 0o7777 == 0o700
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["images"] == dict(zip(("openvox", "vault", "unsealer"), IMAGES))
    assert manifest["volume_count"] == 12
    assert manifest["archive_count"] == 18
    for archive in manifest["archives"]:
        path = output / archive["file"]
        assert path.stat().st_mode & 0o777 == 0o600
        assert hashlib.sha256(path.read_bytes()).hexdigest() == archive["sha256"]
    for volume in VOLUMES:
        with tarfile.open(output / f"{volume}.tar.gz") as archive:
            state = archive.getmember("./state")
            assert state.uid == state.gid == 999
    with tarfile.open(output / "credentials.tar.gz") as archive:
        assert (
            archive.extractfile("openvox-infra/root-token").read()
            == b"ROOT-TOKEN-CANARY\n"
        )
    assert not (authority / "services-stopped").exists()
    commands = (authority / "commands").read_text()
    assert commands.count(" stop openvox vault-unsealer vault\n") == 1
    assert commands.count(" start vault vault-unsealer openvox\n") == 1
    assert "image save " + " ".join(IMAGES) in commands
    assert commands.index("flock -w 300 9\n") < commands.index("docker ")
    for forbidden in (
        "ROOT-TOKEN-CANARY",
        "PRIVATE-VOLUME-CANARY",
        "MUST-NOT-REACH-COMPOSE",
    ):
        assert forbidden not in result.stdout + result.stderr + json.dumps(manifest)


def test_backup_preserves_installed_renewal_units(authority: Path) -> None:
    """Restore kits retain scheduling alongside the established host configuration."""
    units = authority / "etc/systemd/system"
    units.mkdir(parents=True)
    names = ("atlas-openvox-renew.service", "atlas-openvox-renew.timer")
    for name in names:
        (units / name).write_text((SCRIPT.parent / "systemd" / name).read_text())
    result = backup(authority)
    assert result.returncode == 0, result.stderr
    with tarfile.open(
        authority / "scratch/vault/snapshot/host-config.tar.gz"
    ) as archive:
        for name in names:
            assert (
                archive.extractfile(f"etc/systemd/system/{name}").read().decode()
                == (units / name).read_text()
            )


@pytest.mark.parametrize(
    "failure", ["archive", "credentials", "images", "stop", "restore"]
)
def test_failed_backup_restores_services_and_never_claims_completion(
    authority: Path, failure: str
) -> None:
    """Restore partially stopped services even when an archive command fails."""
    (authority / "failure").write_text(failure)
    result = backup(authority)
    assert result.returncode != 0
    assert "Backup ready:" not in result.stdout
    assert not (authority / "scratch/vault/snapshot/manifest.json").exists()
    assert (
        " start vault vault-unsealer openvox\n" in (authority / "commands").read_text()
    )
    assert (authority / "services-stopped").exists() == (failure == "restore")


@pytest.mark.parametrize(
    "failure",
    [
        "root",
        "hostname",
        "promoted",
        "ready",
        "missing-volume",
        "unmanaged-volume",
        "stopped",
        "image",
        "lock",
        "dirty",
        "existing",
        "escape",
    ],
)
def test_preflight_refuses_unsafe_or_incomplete_backup_before_stopping(
    authority: Path, failure: str
) -> None:
    """Never stop services or overwrite backups when authority checks fail."""
    output = None
    if failure == "root":
        (authority / "uid").write_text("1000")
    elif failure == "hostname":
        (authority / "hostname").write_text("docker.home.arpa")
    elif failure == "promoted":
        (authority / "etc/openvox-infra/promoted").write_text("development")
    elif failure == "ready":
        (authority / "root/openvox-infra/ready.json").unlink()
    elif failure == "dirty":
        (authority / "opt/docker/puppet-control-repo/configuration").write_text(
            "operator edits"
        )
    elif failure == "existing":
        (authority / "scratch/vault/snapshot").mkdir(parents=True)
    elif failure == "escape":
        output = str(authority / "scratch/vault/../../outside")
    else:
        (authority / "failure").write_text(failure)
    result = backup(authority, output)
    assert result.returncode != 0
    commands = authority / "commands"
    assert not commands.exists() or " stop " not in commands.read_text()
    assert not (authority / "services-stopped").exists()
