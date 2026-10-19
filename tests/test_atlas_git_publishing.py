"""Exercise Atlas and legacy publishing with real disposable Git repositories."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest


PROXMOX = Path(__file__).resolve().parents[1] / "proxmox"
GITHUB = "git@github.com:Enucatl/puppet-control-repo.git"


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


@pytest.fixture
def publishing(tmp_path: Path) -> Path:
    """Create a shallow Atlas clone and replace host commands, retaining real Git."""
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "-q", "--initial-branch=production")
    base = commit(seed, "production base")
    git(seed, "switch", "-qc", "dev")
    revision = commit(seed, "published dev target")
    github = tmp_path / "github.git"
    git(tmp_path, "clone", "-q", "--bare", str(seed), str(github))
    checkout = tmp_path / "opt/docker/puppet-control-repo"
    checkout.parent.mkdir(parents=True)
    git(
        tmp_path,
        "clone",
        "-q",
        "--depth=1",
        "--branch=production",
        github.as_uri(),
        str(checkout),
    )
    git(checkout, "checkout", "-q", "--detach", base)
    (tmp_path / "revision").write_text(revision)
    (tmp_path / "base").write_text(base)
    (tmp_path / "uid").write_text("0")
    (tmp_path / "hostname").write_text("atlas.home.arpa")
    bins = tmp_path / "bin"
    bins.mkdir()
    commands = {
        "id": 'cat "$TEST_ROOT/uid"',
        "hostname": 'cat "$TEST_ROOT/hostname"',
        "runuser": '[[ $1 == -u && $2 == user_l && $3 == -- ]]; shift 3; exec "$@"',
        "chown": ":",
        "install": r"""
arguments=()
while (( $# )); do
    case "$1" in
        -o|-g) shift 2 ;;
        *) arguments+=("$1"); shift ;;
    esac
done
exec /usr/bin/install "${arguments[@]}"
""",
        "ssh": '[[ "$*" == "-o BatchMode=yes user_l@docker.home.arpa bash -se" ]]; exec bash -se',
    }
    for name, body in commands.items():
        command = bins / name
        command.write_text("#!/bin/bash\nset -eu\n" + body + "\n")
        command.chmod(0o755)
    return tmp_path


def run_script(
    root: Path, name: str = "atlas-publish.sh", *arguments: str
) -> subprocess.CompletedProcess[str]:
    """Run a copied script with only its host paths and GitHub endpoint replaced."""
    source = (PROXMOX / name).read_text()
    source = source.replace(GITHUB, (root / "github.git").as_uri())
    source = source.replace("/opt/", str(root / "opt") + "/")
    script = root / name
    script.write_text(source)
    return subprocess.run(
        ["bash", str(script), *arguments],
        env={
            **os.environ,
            "PATH": f"{root / 'bin'}:{os.environ['PATH']}",
            "TEST_ROOT": str(root),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
        },
        text=True,
        capture_output=True,
        timeout=15,
    )


def test_atlas_publishes_both_targets_and_repeats_safely(publishing: Path) -> None:
    """Publish a shallow detached checkout without introducing receiver hooks."""
    revision = (publishing / "revision").read_text()
    checkout = publishing / "opt/docker/puppet-control-repo"
    bare = publishing / "opt/git/openvox/control.git"
    for _ in range(2):
        result = run_script(publishing, "atlas-publish.sh", revision)
        assert result.returncode == 0, result.stderr
        for repository in (checkout, bare, publishing / "github.git"):
            assert git(repository, "rev-parse", "refs/heads/production") == revision
        assert git(checkout, "branch", "--show-current") == "production"
        assert git(checkout, "status", "--porcelain") == ""
        assert git(checkout, "rev-parse", "--is-shallow-repository") == "false"
        assert git(
            checkout, "config", "--get-all", "remote.origin.pushurl"
        ).splitlines() == [
            (publishing / "github.git").as_uri(),
            str(bare),
        ]
        assert git(checkout, "config", "remote.origin.push") == (
            "refs/heads/production:refs/heads/production"
        )
        known_hosts = checkout / ".git/github-known-hosts"
        assert known_hosts.read_text().startswith("github.com ssh-ed25519 ")
        assert git(checkout, "config", "core.sshCommand") == (
            "ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "
            f"-o UserKnownHostsFile={known_hosts}"
        )
        assert git(checkout, "rev-parse", "--abbrev-ref", "@{upstream}") == (
            "origin/production"
        )
        assert bare.stat().st_mode & 0o7777 == 0o2770
        assert not any(path.is_file() for path in (bare / "hooks").glob("*"))


def test_atlas_preserves_dirty_checkout_without_publishing(publishing: Path) -> None:
    """Refuse publication without changing tracked or untracked operator work."""
    checkout = publishing / "opt/docker/puppet-control-repo"
    (checkout / "fixture").write_text("operator edits")
    (checkout / "untracked").write_text("operator notes")
    before = git(checkout, "status", "--porcelain")
    result = run_script(
        publishing, "atlas-publish.sh", (publishing / "revision").read_text()
    )
    assert result.returncode != 0
    assert git(checkout, "status", "--porcelain") == before
    assert (checkout / "fixture").read_text() == "operator edits"
    assert (checkout / "untracked").read_text() == "operator notes"
    assert (
        git(publishing / "github.git", "rev-parse", "production")
        == (publishing / "base").read_text()
    )
    assert not (publishing / "opt/git/openvox/control.git").exists()


@pytest.mark.parametrize("divergent", ["HEAD", "production", "github", "bare"])
def test_atlas_refuses_divergent_production_history(
    publishing: Path, divergent: str
) -> None:
    """Never overwrite divergent commits in any checkout or publication target."""
    checkout = publishing / "opt/docker/puppet-control-repo"
    seed = publishing / "seed"
    git(seed, "checkout", "-q", "--detach", (publishing / "base").read_text())
    other = commit(seed, "independent production change")
    git(checkout, "fetch", str(seed), other)
    if divergent == "HEAD":
        git(checkout, "checkout", "-q", "--detach", other)
    elif divergent == "production":
        git(checkout, "update-ref", "refs/heads/production", other)
    elif divergent == "github":
        git(
            seed,
            "push",
            str(publishing / "github.git"),
            f"{other}:refs/heads/production",
        )
    else:
        bare = publishing / "opt/git/openvox/control.git"
        bare.parent.mkdir(parents=True)
        git(publishing, "init", "-q", "--bare", "--template=", str(bare))
        git(seed, "push", str(bare), f"{other}:refs/heads/production")
    old_head = git(checkout, "rev-parse", "HEAD")
    old_remote = git(publishing / "github.git", "rev-parse", "production")
    result = run_script(
        publishing, "atlas-publish.sh", (publishing / "revision").read_text()
    )
    assert result.returncode != 0
    assert "Refusing to replace commits" in result.stderr
    assert git(checkout, "rev-parse", "HEAD") == old_head
    assert git(publishing / "github.git", "rev-parse", "production") == old_remote
    if divergent == "bare":
        assert git(bare, "rev-parse", "production") == other


@pytest.mark.parametrize("failure", ["revision", "root", "hostname"])
def test_atlas_rejects_invalid_invocation_before_publishing(
    publishing: Path, failure: str
) -> None:
    """Require a full revision, root and the Atlas hostname before Git changes."""
    revision = (publishing / "revision").read_text()
    if failure == "revision":
        revision = "--all"
    else:
        (publishing / failure.replace("root", "uid")).write_text(
            "1000" if failure == "root" else "docker.home.arpa"
        )
    result = run_script(publishing, "atlas-publish.sh", revision)
    assert result.returncode != 0
    assert not (publishing / "opt/git/openvox/control.git").exists()
    assert (
        git(publishing / "github.git", "rev-parse", "production")
        == (publishing / "base").read_text()
    )


def test_atlas_requires_revision_published_on_dev(publishing: Path) -> None:
    """Reject a known local commit that GitHub dev does not contain."""
    checkout = publishing / "opt/docker/puppet-control-repo"
    revision = commit(publishing / "seed", "unpublished local commit")
    git(checkout, "fetch", str(publishing / "seed"), revision)
    result = run_script(publishing, "atlas-publish.sh", revision)
    assert result.returncode != 0
    assert "must already be published on GitHub dev" in result.stderr
    assert (
        git(publishing / "github.git", "rev-parse", "production")
        == (publishing / "base").read_text()
    )
    assert not (publishing / "opt/git/openvox/control.git").exists()


def test_atlas_refuses_existing_active_receiver_hook(publishing: Path) -> None:
    """Prevent publication from executing an existing receiver deployment hook."""
    bare = publishing / "opt/git/openvox/control.git"
    bare.parent.mkdir(parents=True)
    git(publishing, "init", "-q", "--bare", "--template=", str(bare))
    hook = bare / "hooks/pre-receive"
    hook.parent.mkdir()
    marker = publishing / "hook-executed"
    hook.write_text(f"#!/bin/bash\ntouch '{marker}'\n")
    hook.chmod(0o755)
    result = run_script(
        publishing, "atlas-publish.sh", (publishing / "revision").read_text()
    )
    assert result.returncode != 0
    assert not marker.exists()
    assert (
        git(publishing / "github.git", "rev-parse", "production")
        == (publishing / "base").read_text()
    )


def test_legacy_rerouting_preserves_work_and_bare_refs(publishing: Path) -> None:
    """Keep legacy production isolated while retaining edits and tracking refs."""
    checkout = publishing / "opt/docker/puppet-control-repo"
    bare = publishing / "opt/git/puppet-control-repo.git"
    bare.parent.mkdir(parents=True)
    git(publishing, "clone", "-q", "--bare", str(publishing / "github.git"), str(bare))
    git(bare, "remote", "set-url", "origin", (publishing / "github.git").as_uri())
    revision = (publishing / "revision").read_text()
    git(bare, "update-ref", "refs/remotes/origin/dev", revision)
    (checkout / "fixture").write_text("unsaved legacy work")
    (checkout / "untracked").write_text("legacy notes")
    old_head = git(checkout, "rev-parse", "HEAD")
    old_status = git(checkout, "status", "--porcelain")
    legacy_url = f"user_l@docker.home.arpa:{bare}"
    for _ in range(2):
        result = run_script(publishing, "legacy-git-remotes.sh", str(checkout))
        assert result.returncode == 0, result.stderr
        assert git(checkout, "remote", "get-url", "origin") == legacy_url
        assert git(checkout, "remote", "get-url", "--push", "--all", "origin") == (
            legacy_url
        )
        assert git(checkout, "config", "remote.origin.push") == (
            "refs/heads/production:refs/heads/production"
        )
        assert git(bare, "remote") == ""
        assert git(bare, "rev-parse", "refs/remotes/origin/dev") == revision
        assert git(checkout, "rev-parse", "HEAD") == old_head
        assert git(checkout, "status", "--porcelain") == old_status
        assert (checkout / "fixture").read_text() == "unsaved legacy work"
        assert (checkout / "untracked").read_text() == "legacy notes"
