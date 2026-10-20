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
IPA_IMAGE = "sha256:" + "1" * 64
IPA_SOURCE = "ghcr.io/enucatl/freeipa-server@sha256:" + "2" * 64
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
    dispatcher = bare / "hooks/post-receive"
    dispatcher.write_text(
        (SCRIPT.parent.parent / "docker/openvox/infra-host-post-receive").read_text()
    )
    dispatcher.chmod(0o755)
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
    systemctl)
        case "$*" in
            'is-active --quiet puppet.service') [[ -f $root/puppet-active ]] ;;
            'stop puppet.service')
                [[ -f $root/puppet-active && ! -f $root/services-stopped ]]
                rm "$root/puppet-active"
                [[ $failure != puppet-stop ]]
                ;;
            'start puppet.service')
                [[ ! -f $root/puppet-masked && ! -f $root/services-stopped && $failure != puppet-start ]]
                touch "$root/puppet-active"
                ;;
            *) exit 99 ;;
        esac
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
                if [[ ${@: -1} == infra-ipa-1 && $3 != *State.Health* ]]; then
                    running=true
                    [[ ! -f $root/ipa-initially-stopped ]] || running=false
                    printf 'sha256:%064d %s\n' 0 "$running" | tr 0 1
                    exit
                fi
                if [[ $3 == *State.Health* && ${@: -1} == infra-ipa-1 ]]; then
                    printf 'true healthy\ntrue healthy\ntrue healthy\n'
                    if [[ $failure == ipa-unhealthy ]]; then echo 'true unhealthy'
                    else echo 'true healthy'; fi
                    exit
                fi
                if [[ $3 == *State.Health* ]]; then
                    if [[ $failure == exited ]]; then
                        printf 'false unhealthy\ntrue healthy\ntrue healthy\n'
                    elif [[ $failure == unhealthy || $failure == archive-unhealthy ]]; then
                        printf 'true healthy\ntrue unhealthy\ntrue healthy\n'
                    else
                        printf 'true healthy\ntrue healthy\ntrue healthy\n'
                    fi
                    exit
                fi
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
                [[ "$*" == *'--env-file /dev/null -p infra'* ]]
                if [[ "$*" == *'docker/freeipa/atlas-compose.yaml '* ]]; then
                    [[ -z ${DOCKER_DOMAIN:-} ]]
                else
                    [[ $DOCKER_DOMAIN == home.arpa ]]
                    [[ "$*" == *'docker/docker-compose.yml -f '* && "$*" == *'docker/openvox/infra-vault.override.yaml '* ]]
                fi
                case "$*" in
                    *' stop ipa')
                        touch "$root/ipa-stopped"
                        [[ $failure != ipa-stop ]] || exit 42
                        ;;
                    *' start ipa')
                        [[ ! -f $root/ipa-initially-stopped && $failure != ipa-restore ]]
                        rm -f "$root/ipa-stopped"
                        ;;
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
                if [[ "$*" == *'--entrypoint /bin/cat '* ]]; then
                    cat "$root/volumes/infra_ipa_data/.atlas-freeipa-volume"
                    exit
                fi
                if [[ "$*" == *src=infra_ipa_data,* ]]; then
                    [[ -f $root/ipa-stopped || -f $root/ipa-initially-stopped ]]
                fi
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
                [[ $failure != archive && $failure != archive-unhealthy ]] || exit 42
                if [[ $failure == drift && ! -f $root/drifted ]]; then
                    touch "$root/drifted"
                    for repository in "$root/opt/docker/puppet-control-repo" "$root/opt/docker/compose-security-baseline"; do
                        printf changed > "$repository/configuration"
                        git -C "$repository" add configuration
                        git -C "$repository" -c user.name=Test -c user.email=test@example.invalid commit -qm drift
                    done
                fi
                exec /usr/bin/tar --numeric-owner --owner=999 --group=999 -czf - -C "$root/volumes/$volume" .
                ;;
            *) exit 99 ;;
        esac
        ;;
    *) exit 99 ;;
esac
"""
    )
    for name in ("id", "hostname", "docker", "tar", "flock", "systemctl"):
        path = bins / name
        path.write_text(double)
        path.chmod(0o755)
    source = SCRIPT.read_text()
    for prefix in ("/scratch/vault", "/etc/", "/root", "/opt/", "/run/lock/"):
        source = source.replace(prefix, str(tmp_path) + prefix)
    source = source.replace('/"$config"', str(tmp_path) + '/"$config"')
    source = source.replace("SECONDS + 300", "SECONDS + 1").replace(
        "sleep 2", "sleep 0.05"
    )
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
    commands = (authority / "commands").read_text()
    assert "systemctl stop puppet.service\n" not in commands
    assert "systemctl start puppet.service\n" not in commands


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


@pytest.mark.parametrize("failure", ["unhealthy", "exited", "archive-unhealthy"])
def test_backup_requires_healthy_service_restoration(
    authority: Path, failure: str
) -> None:
    """Both normal restoration and failure cleanup must verify all service health."""
    (authority / "failure").write_text(failure)
    result = backup(authority)
    assert result.returncode != 0
    assert "Backup ready:" not in result.stdout
    assert "restore healthy established Atlas services" in result.stderr
    assert not (authority / "scratch/vault/snapshot/manifest.json").exists()
    commands = (authority / "commands").read_text()
    assert "State.Health.Status" in commands
    assert "infra-openvox-1 infra-vault-1 infra-vault-unsealer-1" in commands


def test_backup_archives_captured_commits_even_if_checkout_advances(
    authority: Path,
) -> None:
    """Manifest commit IDs and archives agree even after an external checkout move."""
    revisions = {
        name: git(authority / "opt/docker" / name, "rev-parse", "HEAD")
        for name in ("puppet-control-repo", "compose-security-baseline")
    }
    (authority / "failure").write_text("drift")
    result = backup(authority)
    assert result.returncode == 0, result.stderr
    output = authority / "scratch/vault/snapshot"
    manifest = json.loads((output / "manifest.json").read_text())
    for repository, archive_name, field in (
        ("puppet-control-repo", "control", "control_revision"),
        ("compose-security-baseline", "hardening", "baseline_revision"),
    ):
        assert manifest[field] == revisions[repository]
        assert (
            git(authority / "opt/docker" / repository, "rev-parse", "HEAD")
            != revisions[repository]
        )
        with tarfile.open(output / f"{archive_name}.tar.gz") as archive:
            assert archive.extractfile("configuration").read().decode() == repository


def test_backup_refuses_receiver_without_deployment_dispatcher(authority: Path) -> None:
    """Require the post-receive dispatcher, not a pre-receive push guard."""
    (authority / "opt/git/openvox/control.git/hooks/post-receive").unlink()
    result = backup(authority)
    assert result.returncode != 0
    assert "managed deployment dispatcher" in result.stderr
    assert " stop " not in (authority / "commands").read_text()


def prepare_ipa(authority: Path, running: bool = True) -> None:
    """Add an established IPA authority with independently identifiable state."""
    state = authority / "root/openvox-infra/freeipa"
    state.mkdir()
    (state / "ready.json").write_text(
        json.dumps({"hostname": "ipa.home.arpa", "root_sha256": "e" * 64})
    )
    (state / "volume.json").write_text(json.dumps({"identity": "original-ipa"}))
    (state / "admin-password").write_text("IPA-PRIVATE-CANARY")
    volume = authority / "volumes/infra_ipa_data"
    volume.mkdir()
    (volume / ".atlas-freeipa-volume").write_text("original-ipa\n")
    (volume / "directory.db").write_text("IPA-DIRECTORY-CANARY")
    repository = authority / "opt/docker/puppet-control-repo"
    dockerfile = repository / "docker/freeipa/atlas.Dockerfile"
    dockerfile.parent.mkdir()
    dockerfile.write_text("FROM " + IPA_SOURCE + "\n")
    git(repository, "add", ".")
    git(
        repository,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "Established IPA",
    )
    if not running:
        (authority / "ipa-initially-stopped").touch()


@pytest.mark.parametrize("running", [True, False])
def test_backup_preserves_established_ipa_without_starting_stopped_server(
    authority: Path, running: bool
) -> None:
    """Archive original directory and private bootstrap state with exact images."""
    prepare_ipa(authority, running)
    result = backup(authority)
    assert result.returncode == 0, result.stderr
    output = authority / "scratch/vault/snapshot"
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["volume_count"] == 13
    assert manifest["archive_count"] == 19
    assert manifest["images"]["ipa"] == IPA_IMAGE
    assert manifest["ipa_source_reference"] == IPA_SOURCE
    with tarfile.open(output / "infra_ipa_data.tar.gz") as archive:
        assert archive.extractfile("./directory.db").read() == b"IPA-DIRECTORY-CANARY"
        assert (
            archive.extractfile("./.atlas-freeipa-volume").read() == b"original-ipa\n"
        )
    with tarfile.open(output / "credentials.tar.gz") as archive:
        assert (
            archive.extractfile("openvox-infra/freeipa/admin-password").read()
            == b"IPA-PRIVATE-CANARY"
        )
    commands = (authority / "commands").read_text()
    assert commands.count(" stop ipa\n") == int(running)
    assert commands.count(" start ipa\n") == int(running)
    assert "IPA-PRIVATE-CANARY" not in result.stdout + result.stderr
    assert "IPA-DIRECTORY-CANARY" not in result.stdout + result.stderr
    assert "volume create" not in commands


@pytest.mark.parametrize(
    "failure", ["missing-volume", "wrong-identity", "missing-checkpoint"]
)
def test_backup_refuses_lost_ipa_identity_before_stopping(
    authority: Path, failure: str
) -> None:
    """An established authority cannot be backed up as empty replacement state."""
    prepare_ipa(authority)
    if failure == "wrong-identity":
        (authority / "volumes/infra_ipa_data/.atlas-freeipa-volume").write_text(
            "replacement"
        )
    elif failure == "missing-checkpoint":
        (authority / "root/openvox-infra/freeipa/volume.json").unlink()
    else:
        (authority / "volumes/infra_ipa_data/.atlas-freeipa-volume").unlink()
        (authority / "volumes/infra_ipa_data/directory.db").unlink()
        (authority / "volumes/infra_ipa_data").rmdir()
    result = backup(authority)
    assert result.returncode != 0
    commands = (authority / "commands").read_text()
    assert " stop " not in commands
    assert "volume create" not in commands


@pytest.mark.parametrize(
    "failure", ["archive", "restore", "ipa-stop", "ipa-restore", "ipa-unhealthy"]
)
def test_failed_backup_restores_previously_running_ipa_and_checks_health(
    authority: Path, failure: str
) -> None:
    """Partial stopping and archive failures retain the original IPA restart set."""
    prepare_ipa(authority)
    (authority / "failure").write_text(failure)
    result = backup(authority)
    assert result.returncode != 0
    assert "Backup ready:" not in result.stdout
    assert not (authority / "scratch/vault/snapshot/manifest.json").exists()
    commands = (authority / "commands").read_text()
    assert " start ipa\n" in commands
    assert (authority / "ipa-stopped").exists() == (failure == "ipa-restore")


def test_backup_does_not_rewrite_checkout_indexes(authority: Path) -> None:
    """A read-only cleanliness check must not leave Git's index owned by root."""
    indexes = {}
    for repository in ("puppet-control-repo", "compose-security-baseline"):
        checkout = authority / "opt/docker" / repository
        index = checkout / ".git/index"
        indexes[index] = (index.stat().st_ino, index.read_bytes())
        tracked = checkout / "configuration"
        previous = tracked.stat().st_mtime_ns
        os.utime(tracked, ns=(previous + 2_000_000_000, previous + 2_000_000_000))
    result = backup(authority)
    assert result.returncode == 0, result.stderr
    for index, before in indexes.items():
        assert (index.stat().st_ino, index.read_bytes()) == before


@pytest.mark.parametrize("active", [True, False])
def test_backup_preserves_native_agent_identities_without_host_caches(
    authority: Path,
    active: bool,
) -> None:
    """Retain enrollment keys, trust and configuration in the private host archive."""
    if active:
        (authority / "puppet-active").touch()
    files = {
        "etc/puppetlabs/puppet/puppet.conf": "[main]\ncertname = atlas.home.arpa\n",
        "etc/puppetlabs/puppet/ssl/private_keys/atlas.home.arpa.pem": "PUPPET-PRIVATE-KEY-CANARY",
        "etc/facter/facts.d/node_type.yaml": "node_type: docker\n",
        "etc/ipa/default.conf": "[global]\nserver = ipa.home.arpa\n",
        "etc/ipa/ca.crt": "IPA-CA-CANARY",
        "etc/krb5.conf": "includedir /etc/krb5.conf.d/\n",
        "etc/krb5.keytab": "KRB5-PRIVATE-KEYTAB-CANARY",
        "etc/ssh/ssh_host_ed25519_key": "SSH-HOST-PRIVATE-KEY-CANARY",
        "etc/ssh/ssh_host_ed25519_key.pub": "SSH-HOST-PUBLIC-KEY-CANARY",
        "etc/krb5.conf.d/ipa.conf": "default_realm = HOME.ARPA\n",
        "etc/sssd/sssd.conf": "SSSD-PRIVATE-CONFIG-CANARY",
        "etc/ssl/vault-secrets/atlas.json": '{"private_key":"VAULT-CERT-PRIVATE-STATE-CANARY"}',
        "var/lib/ipa-client/sysrestore/sysrestore.state": "IPA-SYSRESTORE-CANARY",
        "var/lib/ipa-client/pki/ca-bundle.pem": "IPA-PKI-CA-CANARY",
        "etc/systemd/system/puppet.service.d/openssl.conf": "[Service]\nEnvironment=OPENSSL_CONF=/etc/ssl/openssl.cnf\n",
        "usr/local/share/ca-certificates/atlas-infrastructure.crt": "ATLAS-PUBLIC-CA-CANARY",
        "usr/local/libexec/openvox-child-policy.rb": "NATIVE-CHILD-POLICY-CANARY",
        "etc/puppetlabs/facter/facter.conf": "FACTER-BOOTSTRAP-CONFIG-CANARY",
        "usr/local/lib/facter-no-dhcpcd/dhcpcd": "FACTER-DHCP-SHIM-CANARY",
        "opt/docker/traefik/data/certs/atlas_fullchain.pem": "TRAEFIK-CERT-CANARY",
        "opt/docker/traefik/data/certs/atlas_key.pem": "TRAEFIK-PRIVATE-KEY-CANARY",
        "opt/docker/airflow/secrets/fullchain.pem": "AIRFLOW-CERT-CANARY",
        "opt/docker/airflow/secrets/key.pem": "AIRFLOW-PRIVATE-KEY-CANARY",
        "opt/docker/puppet-control-repo/docker/wolf/secrets/fullchain.pem": "WOLF-CERT-CANARY",
        "opt/docker/puppet-control-repo/docker/wolf/secrets/key.pem": "WOLF-PRIVATE-KEY-CANARY",
        "opt/docker/browser-use/secrets/browser-use.crt": "BROWSER-CERT-CANARY",
        "opt/docker/browser-use/secrets/browser-use.key": "BROWSER-PRIVATE-KEY-CANARY",
        "home/user/.config/home-arpa/mtls/codex.crt": "CODEX-CERT-CANARY",
        "home/user/.config/home-arpa/mtls/codex.key": "CODEX-PRIVATE-KEY-CANARY",
    }
    omitted = {
        "var/lib/sss/db/cache_home.arpa.ldb": "SSSD-CACHE-CANARY",
        "var/cache/puppet/catalog.json": "PUPPET-CACHE-CANARY",
        "etc/shadow": "SHADOW-CANARY",
        "etc/ssh/ssh_config": "UNRELATED-SSH-CONFIG-CANARY",
        "etc/ssh/ssh_known_hosts": "SSH-HOST-CACHE-CANARY",
        "opt/docker/airflow/secrets/unrelated-secret": "UNRELATED-APP-SECRET-CANARY",
        "opt/docker/traefik/data/acme.json": "ACME-STATE-CANARY",
        "home/user/.ssh/id_ed25519": "USER-SSH-KEY-CANARY",
        "usr/local/libexec/unrelated-helper": "UNRELATED-HELPER-CANARY",
    }
    (authority / "opt/docker/puppet-control-repo/.git/info/exclude").write_text(
        "docker/wolf/secrets/\n"
    )
    for name, content in (files | omitted).items():
        path = authority / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        path.chmod(0o755 if name == "usr/local/lib/facter-no-dhcpcd/dhcpcd" else 0o600)
        if name.startswith("etc/ssl/vault-secrets/"):
            path.parent.chmod(0o700)
    result = backup(authority)
    assert result.returncode == 0, result.stderr
    output = authority / "scratch/vault/snapshot"
    archive_path = output / "host-config.tar.gz"
    assert archive_path.stat().st_mode & 0o777 == 0o600
    with tarfile.open(archive_path) as archive:
        assert archive.getmember("etc/ssl/vault-secrets").mode == 0o700
        for name, content in files.items():
            member = archive.getmember(name)
            source = (authority / name).stat()
            assert member.mode == source.st_mode & 0o777
            assert (member.uid, member.gid) == (source.st_uid, source.st_gid)
            assert archive.extractfile(member).read() == content.encode()
        assert not (set(omitted) & set(archive.getnames()))
    assert (authority / "puppet-active").exists() == active
    public_output = (
        result.stdout + result.stderr + (output / "manifest.json").read_text()
    )
    for canary in (*files.values(), *omitted.values()):
        assert canary not in public_output


@pytest.mark.parametrize("state", ["active", "inactive", "masked"])
def test_backup_restores_original_native_agent_state(
    authority: Path, state: str
) -> None:
    """Quiesce only an active daemon and restart it after healthy authorities."""
    if state != "inactive":
        (authority / f"puppet-{state}").touch()
    result = backup(authority)
    assert result.returncode == 0, result.stderr
    commands = (authority / "commands").read_text()
    stop = "systemctl stop puppet.service\n"
    start = "systemctl start puppet.service\n"
    if state == "active":
        assert commands.count(stop) == commands.count(start) == 1
        assert commands.index(stop) < commands.index(
            " stop openvox vault-unsealer vault\n"
        )
        assert commands.index(start) > commands.index("State.Health.Status")
        assert commands.index(start) < commands.index("image save")
    else:
        assert stop not in commands and start not in commands
    assert (authority / "puppet-active").exists() == (state == "active")
    assert (authority / "puppet-masked").exists() == (state == "masked")


@pytest.mark.parametrize(
    "failure",
    ["archive", "credentials", "images", "puppet-stop", "puppet-start", "restore"],
)
def test_failed_backup_restores_native_agent_only_after_authorities(
    authority: Path, failure: str
) -> None:
    """Failure cleanup restores the daemon unless authoritative recovery failed."""
    (authority / "puppet-active").touch()
    (authority / "failure").write_text(failure)
    result = backup(authority)
    assert result.returncode != 0
    assert "Backup ready:" not in result.stdout
    assert not (authority / "scratch/vault/snapshot/manifest.json").exists()
    commands = (authority / "commands").read_text()
    assert commands.count("systemctl stop puppet.service\n") == 1
    if failure == "restore":
        assert "systemctl start puppet.service\n" not in commands
    else:
        assert "systemctl start puppet.service\n" in commands
        assert commands.index("systemctl start puppet.service\n") > commands.index(
            "State.Health.Status"
        )
    assert (authority / "puppet-active").exists() == (
        failure not in {"restore", "puppet-start"}
    )


def test_backup_keeps_optional_identity_symlinks(authority: Path) -> None:
    """Preserve existing key links without dereferencing or archiving other SSH state."""
    directory = authority / "etc/ssh"
    directory.mkdir()
    target = authority / "external-ssh-key"
    target.write_text("EXTERNAL-SSH-KEY-CANARY")
    (directory / "ssh_host_rsa_key").symlink_to(target)
    (directory / "ssh_host_rsa_key.pub").symlink_to("missing-public-key")
    linked = {
        "usr/local/libexec/openvox-child-policy.rb": "missing-child-policy",
        "home/user/.config/home-arpa/mtls/codex.key": str(target),
        "opt/docker/browser-use/secrets/browser-use.crt": "missing-public-cert",
    }
    for name, link in linked.items():
        path = authority / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(link)
    result = backup(authority)
    assert result.returncode == 0, result.stderr
    output = authority / "scratch/vault/snapshot"
    with tarfile.open(output / "host-config.tar.gz") as archive:
        key = archive.getmember("etc/ssh/ssh_host_rsa_key")
        assert key.issym() and key.linkname == str(target)
        public = archive.getmember("etc/ssh/ssh_host_rsa_key.pub")
        assert public.issym() and public.linkname == "missing-public-key"
        assert "external-ssh-key" not in archive.getnames()
        for name, link in linked.items():
            member = archive.getmember(name)
            assert member.issym() and member.linkname == link
    assert "EXTERNAL-SSH-KEY-CANARY" not in result.stdout + result.stderr
