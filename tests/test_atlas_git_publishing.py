"""Exercise ordinary Atlas publication and the live OpenVox receiver."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest


PROXMOX = Path(__file__).resolve().parents[1] / "proxmox"


def git(repository: Path, *arguments: str) -> str:
    """Run Git against an isolated worktree or bare repository."""
    return subprocess.check_output(
        ["git", "-C", str(repository), *arguments], text=True, stderr=subprocess.PIPE
    ).strip()


def commit(repository: Path, content: str) -> str:
    """Commit one fixture change and return its complete object ID."""
    (repository / "fixture").write_text(content)
    git(repository, "add", "fixture")
    git(
        repository,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        content,
    )
    return git(repository, "rev-parse", "HEAD")


@pytest.mark.parametrize("failure", ["", "deployment", "wrong-commit"])
def test_ordinary_push_dispatches_deployment_and_records_result(
    tmp_path: Path, failure: str
) -> None:
    """Keep both Git targets and the deployment receipt observable after a push."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    git(checkout, "init", "-q", "--initial-branch=production")
    revision = commit(checkout, "production")
    github = tmp_path / "github.git"
    receiver = tmp_path / "control.git"
    for repository in (github, receiver):
        git(tmp_path, "init", "-q", "--bare", "--template=", str(repository))
    git(checkout, "remote", "add", "origin", str(github))
    for repository in (github, receiver):
        git(checkout, "config", "--add", "remote.origin.pushurl", str(repository))
    git(checkout, "config", "remote.origin.push", "production:production")
    hook = receiver / "hooks/post-receive"
    hook.parent.mkdir()
    hook.write_text(
        (PROXMOX.parent / "docker/openvox/infra-host-post-receive").read_text()
    )
    hook.chmod(0o755)
    bins = tmp_path / "bin"
    bins.mkdir()
    docker = bins / "docker"
    docker.write_text(
        "#!/bin/bash\nset -eu\n"
        + r"""
[[ $* == '--host unix:///var/run/docker.sock exec -i infra-openvox-1 bash /infra/infra-post-receive' ]]
cat > "$TEST_ROOT/deploy-input"
status=0
[[ $FAILURE != deployment ]] || status=42
printf '%s\n' "$status" > "$TEST_ROOT/deploy.status"
if [[ $status == 0 ]]; then
    if [[ $FAILURE == wrong-commit ]]; then
        printf '%040d\n' 0 > "$TEST_ROOT/deploy.commit"
    else
        awk '{print $2}' "$TEST_ROOT/deploy-input" > "$TEST_ROOT/deploy.commit"
    fi
fi
exit "$status"
"""
    )
    docker.chmod(0o755)
    result = subprocess.run(
        ["git", "-C", str(checkout), "push", "origin"],
        env={
            **os.environ,
            "PATH": f"{bins}:{os.environ['PATH']}",
            "TEST_ROOT": str(tmp_path),
            "FAILURE": failure,
        },
        text=True,
        capture_output=True,
        timeout=5,
    )
    # Git accepts refs even if post-receive deployment fails; inspect the receipt.
    assert result.returncode == 0, result.stderr
    for repository in (github, receiver):
        assert git(repository, "rev-parse", "production") == revision
    assert (tmp_path / "deploy-input").read_text().split() == [
        "0" * 40,
        revision,
        "refs/heads/production",
    ]
    assert (tmp_path / "deploy.status").read_text() == (
        "42\n" if failure == "deployment" else "0\n"
    )
    receipt = tmp_path / "deploy.commit"
    if failure == "deployment":
        assert not receipt.exists()
        assert "OpenVox deployment failed (42)" in result.stderr
    else:
        assert receipt.read_text().strip() == (
            "0" * 40 if failure == "wrong-commit" else revision
        )


def runtime_script(root: Path, name: str) -> Path:
    """Redirect a container script's absolute state paths into a temporary tree."""
    source = (PROXMOX.parent / "docker/openvox" / name).read_text()
    for prefix in (
        "/infra/",
        "/git/",
        "/etc/puppetlabs/",
        "/opt/puppetlabs/",
        "/trust/",
        "/usr/local/bin/",
    ):
        source = source.replace(prefix, str(root) + prefix)
    script = root / name
    script.write_text(source)
    return script


@pytest.mark.parametrize("failure", ["", "r10k", "puppet", "ruby"])
def test_container_handler_records_commit_only_after_deploy_types_and_flush(
    tmp_path: Path, failure: str
) -> None:
    """Verify deployment order, isolated Git context and persistent failure status."""
    for directory in ("git", "infra", "etc/puppetlabs/code/environments/production"):
        (tmp_path / directory).mkdir(parents=True)
    deployed = tmp_path / "etc/puppetlabs/code/environments/production"
    git(deployed, "init", "-q", "--initial-branch=production")
    (deployed / "hiera.yaml").write_text("Repository Hiera settings\n")
    revision = commit(deployed, "deployed production")
    git(
        tmp_path,
        "clone",
        "-q",
        "--bare",
        str(deployed),
        str(tmp_path / "git/control.git"),
    )
    for command in (
        "opt/puppetlabs/puppet/bin/r10k",
        "opt/puppetlabs/bin/puppet",
        "opt/puppetlabs/puppet/bin/ruby",
    ):
        executable = tmp_path / command
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_text(
            "#!/bin/bash\nset -eu\n"
            + r"""
name=${0##*/}
printf '%s\n' "$name" >> "$TEST_ROOT/steps"
[[ -z ${GIT_DIR:-} && -z ${GIT_WORK_TREE:-} && -z ${GIT_INDEX_FILE:-} && -z ${GIT_COMMON_DIR:-} ]]
if [[ $name == puppet ]]; then
    grep -qx "Repository Hiera settings" "$TEST_ROOT/etc/puppetlabs/code/environments/production/hiera.yaml"
fi
[[ $name != "$FAILURE" ]] || exit 42
"""
        )
        executable.chmod(0o755)
    result = subprocess.run(
        ["bash", str(runtime_script(tmp_path, "infra-post-receive"))],
        input=(
            f"{'0' * 40} {revision} refs/heads/production\n"
            f"{revision} {'0' * 40} refs/heads/dev\n"
            f"{'0' * 40} {revision} refs/heads/unrelated\n"
        ),
        env={
            **os.environ,
            "TEST_ROOT": str(tmp_path),
            "FAILURE": failure,
            **{
                name: "/unrelated-git-state"
                for name in (
                    "GIT_DIR",
                    "GIT_WORK_TREE",
                    "GIT_INDEX_FILE",
                    "GIT_COMMON_DIR",
                )
            },
        },
        text=True,
        capture_output=True,
        timeout=5,
    )
    status = 42 if failure else 0
    assert result.returncode == status, result.stderr
    assert (tmp_path / "git/deploy.status").read_text() == f"{status}\n"
    steps = ["r10k", "puppet", "ruby"]
    assert (tmp_path / "steps").read_text().splitlines() == (
        steps[: steps.index(failure) + 1] if failure else steps
    )
    marker = tmp_path / "git/deploy.commit"
    if failure:
        assert not marker.exists()
    else:
        assert marker.read_text() == revision + "\n"
