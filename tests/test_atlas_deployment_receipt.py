"""Verify deployment receipts against real repositories and overlapping hooks."""

from __future__ import annotations

import os
from pathlib import Path
import select
import subprocess

import pytest

from test_atlas_git_publishing import commit, git, runtime_script


@pytest.fixture
def deployment(tmp_path: Path) -> Path:
    """Create source and deployed repositories with two environment revisions."""
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "-q", "--initial-branch=production")
    commit(seed, "production")
    git(seed, "switch", "-qc", "dev")
    commit(seed, "dev")
    (tmp_path / "git").mkdir()
    bare = tmp_path / "git/control.git"
    git(tmp_path, "clone", "-q", "--bare", str(seed), str(bare))
    for environment in ("production", "dev"):
        checkout = tmp_path / "etc/puppetlabs/code/environments" / environment
        checkout.parent.mkdir(parents=True, exist_ok=True)
        git(tmp_path, "clone", "-q", "--branch", environment, str(bare), str(checkout))
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra/infra-hiera.yaml").write_text("Atlas Hiera settings\n")
    for name in (
        "opt/puppetlabs/puppet/bin/r10k",
        "opt/puppetlabs/bin/puppet",
        "opt/puppetlabs/puppet/bin/ruby",
        "bin/flock",
    ):
        executable = tmp_path / name
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_text(
            "#!/bin/bash\nset -eu\n"
            + r"""
case "${0##*/}" in
  flock)
    printf 'locking\n'
    exec /usr/bin/flock "$@"
    ;;
  r10k)
    environment=$3
    mkdir "$TEST_ROOT/running"
    printf '%s begin\n' "$environment" >> "$TEST_ROOT/steps"
    printf 'deploying %s\n' "$environment"
    if [[ -p $TEST_ROOT/release && $environment == production ]]; then
        read -r _ < "$TEST_ROOT/release"
    fi
    revision=$(git --git-dir="$TEST_ROOT/git/control.git" rev-parse "refs/heads/$environment")
    if [[ -f $TEST_ROOT/wrong-revision ]]; then
        revision=$(cat "$TEST_ROOT/wrong-revision")
    fi
    checkout="$TEST_ROOT/etc/puppetlabs/code/environments/$environment"
    git -C "$checkout" fetch -q origin
    git -C "$checkout" checkout -q --detach "$revision"
    printf '%s end\n' "$environment" >> "$TEST_ROOT/steps"
    rmdir "$TEST_ROOT/running"
    ;;
esac
"""
        )
        executable.chmod(0o755)
    runtime_script(tmp_path, "infra-post-receive")
    return tmp_path


def handler(root: Path, environment: str, revision: str) -> subprocess.Popen[str]:
    """Start one hook with its complete input already available."""
    process = subprocess.Popen(
        ["bash", str(root / "infra-post-receive")],
        env={
            **os.environ,
            "TEST_ROOT": str(root),
            "PATH": f"{root / 'bin'}:{os.environ['PATH']}",
        },
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdin is not None
    process.stdin.write(f"{'0' * 40} {revision} refs/heads/{environment}\n")
    process.stdin.close()
    process.stdin = None
    return process


@pytest.mark.parametrize("failure", ["stale-input", "wrong-checkout"])
def test_receipt_rejects_requested_and_deployed_revision_mismatch(
    deployment: Path, failure: str
) -> None:
    """Reject replayed input and r10k success that leaves a different commit."""
    bare = deployment / "git/control.git"
    requested = git(bare, "rev-parse", "production")
    other = git(bare, "rev-parse", "dev")
    if failure == "stale-input":
        git(bare, "update-ref", "refs/heads/production", other)
    else:
        (deployment / "wrong-revision").write_text(other)
    process = handler(deployment, "production", requested)
    _, stderr = process.communicate(timeout=5)
    assert process.returncode != 0
    assert "stale deployment input" in stderr or "differs from requested" in stderr
    assert (deployment / "git/deploy.status").read_text() == "1\n"
    assert not (deployment / "git/deploy.commit").exists()
    if failure == "stale-input":
        assert not (deployment / "steps").exists()


def test_overlapping_handlers_serialize_deployment_and_receipt(
    deployment: Path,
) -> None:
    """Keep a queued hook outside deployment until the first receipt is finished."""
    os.mkfifo(deployment / "release")
    processes = []

    def expect_output(process: subprocess.Popen[str], expected: str) -> None:
        """Read each byte directly so pipe buffering cannot hide the next event."""
        assert process.stdout is not None
        for character in expected:
            assert select.select([process.stdout], [], [], 5)[0]
            assert os.read(process.stdout.fileno(), 1) == character.encode()

    try:
        for environment in ("production", "dev"):
            revision = git(deployment / "git/control.git", "rev-parse", environment)
            process = handler(deployment, environment, revision)
            processes.append(process)
            expect_output(process, "locking\n")
            if environment == "production":
                expect_output(process, "deploying production\n")
        (deployment / "release").write_text("continue\n")
        for process in processes:
            _, stderr = process.communicate(timeout=5)
            assert process.returncode == 0, stderr
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate()
    assert (deployment / "steps").read_text().splitlines() == [
        "production begin",
        "production end",
        "dev begin",
        "dev end",
    ]
    actual = git(
        deployment / "etc/puppetlabs/code/environments/dev", "rev-parse", "HEAD"
    )
    assert (deployment / "git/deploy.commit").read_text() == actual + "\n"
    assert (deployment / "git/deploy.status").read_text() == "0\n"
