"""Exercise Atlas and legacy publishing with real disposable Git repositories."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError
import fcntl
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
    reference = seed / "docker/openvox/infra-host-post-receive"
    reference.parent.mkdir(parents=True)
    reference.write_text(
        (PROXMOX.parent / "docker/openvox/infra-host-post-receive").read_text()
    )
    git(seed, "add", str(reference))
    base = commit(seed, "production base")
    revision = commit(seed, "published production target")
    github = tmp_path / "github.git"
    git(tmp_path, "clone", "-q", "--bare", str(seed), str(github))
    checkout = tmp_path / "opt/docker/puppet-control-repo"
    checkout.parent.mkdir(parents=True)
    checkout.parent.chmod(0o2770)
    git(
        tmp_path,
        "clone",
        "-q",
        "--depth=2",
        "--branch=production",
        github.as_uri(),
        str(checkout),
    )
    git(checkout, "checkout", "-q", "--detach", base)
    (tmp_path / "revision").write_text(revision)
    (tmp_path / "base").write_text(base)
    (tmp_path / "uid").write_text("0")
    (tmp_path / "hostname").write_text("atlas.home.arpa")
    (tmp_path / "run/lock").mkdir(parents=True)
    bins = tmp_path / "bin"
    bins.mkdir()
    commands = {
        "id": 'cat "$TEST_ROOT/uid"',
        "hostname": 'cat "$TEST_ROOT/hostname"',
        "runuser": r"""
[[ $1 == -u && $2 == user_l && $3 == -- ]]
if [[ -f $TEST_ROOT/root-index && ! -f $TEST_ROOT/index-repaired ]]; then exit 42; fi
shift 3
exec "$@"
""",
        "stat": r"""
if [[ -f $TEST_ROOT/root-index && $* == *'.git/index' ]]; then echo 0
else exec /usr/bin/stat "$@"; fi
""",
        "chown": r"""
if [[ ${@: -1} == "$TEST_ROOT/opt/docker/puppet-control-repo/.git/index" ]]; then
    [[ $1 == --no-dereference && $2 == user_l:docker ]]
    touch "$TEST_ROOT/index-repaired"
fi
""",
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
        "docker": r"""
printf '%s\n' "$*" >> "$TEST_ROOT/docker-commands"
case "$*" in
    '--host unix:///var/run/docker.sock exec -i infra-openvox-1 bash /infra/infra-post-receive')
        cat > "$TEST_ROOT/deploy-input"
        status=$(cat "$TEST_ROOT/deploy-exit" 2>/dev/null || printf 0)
        printf '%s\n' "$status" > "$TEST_ROOT/deploy.status"
        if [[ $status == 0 ]]; then
            if [[ -f $TEST_ROOT/reported-revision ]]; then
                cat "$TEST_ROOT/reported-revision" > "$TEST_ROOT/deploy.commit"
            else
                awk '{print $2}' "$TEST_ROOT/deploy-input" > "$TEST_ROOT/deploy.commit"
            fi
        fi
        exit "$status"
        ;;
    '--host unix:///var/run/docker.sock exec infra-openvox-1 cat /git/deploy.status /git/deploy.commit')
        cat "$TEST_ROOT/deploy.status" "$TEST_ROOT/deploy.commit"
        ;;
    *) exit 99 ;;
esac
""",
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
    source = source.replace("/run/lock/", str(root / "run/lock") + "/")
    script = root / name
    script.write_text(source)
    return subprocess.run(
        ["bash", str(script), *arguments],
        env={
            **os.environ,
            "PATH": f"{root / 'bin'}:{os.environ['PATH']}",
            "TEST_ROOT": str(root),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": str(root / "gitconfig-system"),
        },
        text=True,
        capture_output=True,
        timeout=15,
        umask=0o077,
    )


def test_atlas_publishes_both_targets_and_repeats_safely(publishing: Path) -> None:
    """Publish a shallow checkout without installing a receive gate."""
    revision = (publishing / "revision").read_text()
    checkout = publishing / "opt/docker/puppet-control-repo"
    bare = publishing / "opt/git/openvox/control.git"
    for attempt in range(2):
        if attempt:
            for path in [bare, *bare.rglob("*")]:
                path.chmod(path.stat().st_mode & ~0o007)
            revision = commit(publishing / "seed", "next published production target")
            git(
                publishing / "seed",
                "push",
                str(publishing / "github.git"),
                "HEAD:refs/heads/production",
            )
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
            "ssh -o BatchMode=yes -o ProxyCommand=none -o StrictHostKeyChecking=yes "
            f"-o UserKnownHostsFile={known_hosts}"
        )
        assert git(checkout, "rev-parse", "--abbrev-ref", "@{upstream}") == (
            "origin/production"
        )
        assert checkout.parent.stat().st_mode & 0o7777 == 0o2770
        assert bare.parent.stat().st_mode & 0o7777 == 0o2775
        assert bare.stat().st_mode & 0o7777 == 0o2775
        assert git(bare, "config", "core.sharedRepository") == "0664"
        assert subprocess.check_output(
            [
                "git",
                "config",
                "--file",
                str(publishing / "gitconfig-system"),
                "--get-all",
                "safe.directory",
            ],
            text=True,
        ).splitlines() == [str(bare)]
        for path in bare.rglob("*"):
            assert path.stat().st_mode & 0o004, path
            assert not path.stat().st_mode & 0o002, path
            if path.is_dir():
                assert path.stat().st_mode & 0o001, path
        assert sorted(path.name for path in (bare / "hooks").glob("*")) == []


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
        == (publishing / "revision").read_text()
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
            "--force",
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
    expected_error = (
        "must already be published on GitHub production"
        if divergent == "github"
        else "Refusing to replace commits"
    )
    assert expected_error in result.stderr
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
        == (publishing / "revision").read_text()
    )


def test_atlas_requires_revision_published_on_production(publishing: Path) -> None:
    """Reject a commit published only on a different GitHub branch."""
    checkout = publishing / "opt/docker/puppet-control-repo"
    revision = commit(publishing / "seed", "commit published only on another branch")
    git(
        publishing / "seed",
        "push",
        str(publishing / "github.git"),
        f"{revision}:refs/heads/dev",
    )
    git(checkout, "fetch", str(publishing / "seed"), revision)
    result = run_script(publishing, "atlas-publish.sh", revision)
    assert result.returncode != 0
    assert "must already be published on GitHub production" in result.stderr
    assert (
        git(checkout, "for-each-ref", "--format=%(refname)", "refs/remotes/origin/dev")
        == ""
    )
    assert (
        git(publishing / "github.git", "rev-parse", "production")
        == (publishing / "revision").read_text()
    )
    assert not (publishing / "opt/git/openvox/control.git").exists()


@pytest.mark.parametrize("name", ["post-receive"])
def test_atlas_refuses_existing_active_receiver_hook(
    publishing: Path, name: str
) -> None:
    """Prevent publication from executing an existing receiver deployment hook."""
    bare = publishing / "opt/git/openvox/control.git"
    bare.parent.mkdir(parents=True)
    git(publishing, "init", "-q", "--bare", "--template=", str(bare))
    hook = bare / "hooks" / name
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
        == (publishing / "revision").read_text()
    )


@pytest.fixture
def managed_receiver(publishing: Path) -> Path:
    """Install the exact managed dispatcher in an otherwise empty bare receiver."""
    bare = publishing / "opt/git/openvox/control.git"
    bare.parent.mkdir(parents=True)
    git(publishing, "init", "-q", "--bare", "--template=", str(bare))
    hook = bare / "hooks/post-receive"
    hook.parent.mkdir()
    hook.write_text(
        (PROXMOX.parent / "docker/openvox/infra-host-post-receive").read_text()
    )
    hook.chmod(0o755)
    return bare


def test_managed_hook_dispatches_updates_and_publisher_checks_commit(
    publishing: Path, managed_receiver: Path
) -> None:
    """Carry real ref-update stdin into deployment and verify its success marker."""
    revision = (publishing / "revision").read_text()
    for _ in range(2):
        result = run_script(publishing, "atlas-publish.sh", revision)
        assert result.returncode == 0, result.stderr
    assert (publishing / "deploy-input").read_text().split() == [
        "0" * 40,
        revision,
        "refs/heads/production",
    ]
    assert git(managed_receiver, "rev-parse", "production") == revision
    # A no-op push does not rerun the receiver hook or reuse an unrelated old status.
    assert len((publishing / "docker-commands").read_text().splitlines()) == 2


@pytest.mark.parametrize("failure", ["deployment", "wrong-commit"])
def test_publisher_reports_deployment_failure_after_refs_are_accepted(
    publishing: Path, managed_receiver: Path, failure: str
) -> None:
    """Do not equate Git's successful receipt with successful code deployment."""
    if failure == "deployment":
        (publishing / "deploy-exit").write_text("42")
    else:
        (publishing / "reported-revision").write_text("0" * 40)
    revision = (publishing / "revision").read_text()
    result = run_script(publishing, "atlas-publish.sh", revision)
    assert result.returncode != 0
    assert "Git accepted the refs, but OpenVox deployment failed" in result.stderr
    if failure == "deployment":
        assert (
            "OpenVox deployment failed (42) after Git accepted the refs"
            in result.stderr
        )
    assert git(managed_receiver, "rev-parse", "production") == revision
    assert git(publishing / "github.git", "rev-parse", "production") == revision


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


@pytest.mark.parametrize("hook", ["managed", "container", "nonexecutable", "missing"])
@pytest.mark.parametrize(
    "missing",
    [
        None,
        "trust/root.pem",
        "etc/puppetlabs/code/environments/production/.r10k-deploy.json",
    ],
)
def test_startup_requires_serving_state_independently_of_deployment_hook(
    tmp_path: Path, hook: str, missing: str | None
) -> None:
    """Keep serving intact production while deployment wiring or dev code is absent."""
    for name in (
        "trust/root.pem",
        "trust/openvox-chain.pem",
        "etc/puppetlabs/puppetserver/ca/ca_crl.pem",
        "etc/puppetlabs/code/environments/production/.r10k-deploy.json",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("established state\n")
    reference = tmp_path / "infra/infra-host-post-receive"
    reference.parent.mkdir()
    reference.write_text(
        (PROXMOX.parent / "docker/openvox/infra-host-post-receive").read_text()
    )
    installed = tmp_path / "git/control.git/hooks/post-receive"
    installed.parent.mkdir(parents=True)
    installed.write_text(
        (PROXMOX.parent / "docker/openvox/infra-post-receive").read_text()
        if hook == "container"
        else reference.read_text()
    )
    installed.chmod(0o644 if hook == "nonexecutable" else 0o755)
    if hook == "missing":
        installed.unlink()
    if missing:
        (tmp_path / missing).unlink()
    entrypoint = tmp_path / "usr/local/bin/openvox-server-entrypoint"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text('#!/bin/bash\ntouch "$TEST_ROOT/server-started"\n')
    entrypoint.chmod(0o755)
    result = subprocess.run(
        ["bash", str(runtime_script(tmp_path, "infra-start.sh"))],
        env={**os.environ, "TEST_ROOT": str(tmp_path)},
        text=True,
        capture_output=True,
        timeout=5,
    )
    assert (result.returncode == 0) == (missing is None), result.stderr
    assert (tmp_path / "server-started").exists() == (missing is None)


def test_publishing_waits_for_backup_before_checkout_or_ref_changes(
    publishing: Path,
) -> None:
    """The actual publisher must hold maintenance exclusion before any Git mutation."""
    checkout = publishing / "opt/docker/puppet-control-repo"
    before = git(checkout, "show-ref")
    revision = (publishing / "revision").read_text()
    with ThreadPoolExecutor(max_workers=1) as executor:
        with (publishing / "run/lock/atlas-openvox-maintenance.lock").open(
            "a"
        ) as backup:
            fcntl.flock(backup, fcntl.LOCK_EX)
            pending = executor.submit(
                run_script, publishing, "atlas-publish.sh", revision
            )
            try:
                with pytest.raises(TimeoutError):
                    pending.result(timeout=0.2)
                assert git(checkout, "show-ref") == before
                assert not (publishing / "opt/git/openvox/control.git").exists()
                assert not (checkout / ".git/github-known-hosts").exists()
            finally:
                fcntl.flock(backup, fcntl.LOCK_UN)
        result = pending.result(timeout=10)
    assert result.returncode == 0, result.stderr
    assert git(checkout, "rev-parse", "production") == revision


@pytest.mark.parametrize("dirty", [False, True])
def test_publisher_repairs_root_private_index_without_discarding_edits(
    publishing: Path, dirty: bool
) -> None:
    """Recover the earlier backup's ownership change before enforcing cleanliness."""
    (publishing / "root-index").touch()
    checkout = publishing / "opt/docker/puppet-control-repo"
    (checkout / ".git/index").chmod(0o600)
    if dirty:
        (checkout / "fixture").write_text("operator edits")
    result = run_script(
        publishing, "atlas-publish.sh", (publishing / "revision").read_text()
    )
    assert (publishing / "index-repaired").exists()
    assert (result.returncode == 0) == (not dirty), result.stderr
    if dirty:
        assert (checkout / "fixture").read_text() == "operator edits"
        assert (
            git(publishing / "github.git", "rev-parse", "production")
            == (publishing / "revision").read_text()
        )


def test_publisher_does_not_repair_symlink_index(publishing: Path) -> None:
    """Ownership recovery must never follow an index pointing outside the checkout."""
    (publishing / "root-index").touch()
    index = publishing / "opt/docker/puppet-control-repo/.git/index"
    outside = publishing / "external-index"
    index.rename(outside)
    index.symlink_to(outside)
    before = outside.read_bytes()
    result = run_script(
        publishing, "atlas-publish.sh", (publishing / "revision").read_text()
    )
    assert result.returncode != 0
    assert not (publishing / "index-repaired").exists()
    assert outside.read_bytes() == before
