"""Archive disposable OpenVox state and restore it into a separate local project."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile


PROJECT = "openvox-rehearsal"
RESTORE = "openvox-rehearsal-restore"
TOOLS = "openvox-rehearsal-tools:24001"
VOLUMES = (
    "root-state",
    "vault-state",
    "trust",
    "server-ssl",
    "server-ca",
    "server-data",
    "server-gems",
    "code",
    "git",
    "r10k-cache",
    "agent-state",
)


def _digest(path: Path) -> dict:
    """Describe an archive without exposing its contents."""
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    return {"file": path.name, "bytes": path.stat().st_size, "sha256": digest}


def _transfer(arguments: list[str], archive: Path, *, restore: bool = False) -> None:
    """Transfer binary archives through protected files, retaining errors privately."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("COMPOSE_")
    }
    if restore:
        stream = archive.open("rb")
    else:
        stream = os.fdopen(
            os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
        )
    with stream:
        result = subprocess.run(
            arguments,
            stdin=stream if restore else subprocess.DEVNULL,
            stdout=subprocess.PIPE if restore else stream,
            stderr=subprocess.PIPE,
            env=environment,
            timeout=900,
        )
    if result.returncode:
        log = archive.with_name(archive.name + ".failure.log")
        with os.fdopen(
            os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
        ) as output:
            output.write((result.stdout or b"") + result.stderr)
        raise RuntimeError(
            f"Recovery archive command failed; private diagnostics: {log}"
        )


def _save_images(controller, archive: Path) -> None:
    """Save accepted and derived images without requiring registry access at restore."""
    with os.fdopen(
        os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
    ) as output:
        with tempfile.TemporaryFile() as errors:
            with subprocess.Popen(
                controller.DOCKER
                + ["image", "save", controller.REPORT["image"], TOOLS],
                stdout=subprocess.PIPE,
                stderr=errors,
            ) as process:
                try:
                    with gzip.GzipFile(
                        fileobj=output, mode="wb", mtime=0
                    ) as compressed:
                        shutil.copyfileobj(process.stdout, compressed)
                    status = process.wait(timeout=900)
                except BaseException:
                    process.kill()
                    process.wait()
                    raise
                if status:
                    log = archive.with_name(archive.name + ".failure.log")
                    errors.seek(0)
                    with os.fdopen(
                        os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
                    ) as diagnostics:
                        shutil.copyfileobj(errors, diagnostics)
                    raise RuntimeError(
                        f"Saving rehearsal images failed; private diagnostics: {log}"
                    )


def _volume_command(controller, name: str, *, restore: bool) -> list[str]:
    """Mount a single named volume into an offline archive tool container."""
    arguments = controller.DOCKER + [
        "run",
        "--rm",
        "--network",
        "none",
        "--user",
        "0:0",
        "--memory",
        "128m",
        "--pids-limit",
        "64",
        "--security-opt",
        "no-new-privileges:true",
        "--mount",
        f"type=volume,src={name},dst=/volume" + ("" if restore else ",readonly"),
        "--entrypoint",
        "/bin/tar",
        TOOLS,
    ]
    if restore:
        arguments.insert(len(controller.DOCKER) + 2, "-i")
    return arguments + (
        ["-xzf", "-", "--numeric-owner", "-C", "/volume"]
        if restore
        else ["-czf", "-", "--numeric-owner", "-C", "/volume", "."]
    )


def _save_configuration(source: Path, archive: Path) -> None:
    """Archive the sanitized bind-mounted repository without changing its modes."""
    with os.fdopen(
        os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
    ) as output:
        with tarfile.open(fileobj=output, mode="w:gz") as contents:
            contents.add(source, arcname=".")


def _restore_configuration(archive: Path, target: Path) -> None:
    """Extract archived configuration into a new independent bind-mount tree."""
    target.mkdir(mode=0o755)
    with tarfile.open(archive, "r:gz") as contents:
        contents.extractall(target, filter="data")
    target.chmod(0o755)
    for path in target.rglob("*"):
        if path.is_symlink():
            continue
        path.chmod(0o755 if path.is_dir() or path.stat().st_mode & 0o111 else 0o644)


def recover(controller) -> dict:
    """Restore stopped rehearsal identities and catalogs into independent volumes."""
    if controller.PROJECT != PROJECT or controller.DOCKER != [
        "docker",
        "--host",
        "unix:///var/run/docker.sock",
    ]:
        raise RuntimeError("Recovery only accepts the fixed local rehearsal project")
    target_names = {f"{RESTORE}_{name}" for name in VOLUMES}
    existing = set(
        controller.execute(
            controller.DOCKER + ["volume", "ls", "--format", "{{.Name}}"]
        ).splitlines()
    )
    containers = controller.execute(
        controller.DOCKER
        + [
            "ps",
            "-a",
            "--filter",
            f"label=com.docker.compose.project={RESTORE}",
            "--format",
            "{{.ID}}",
        ]
    ).strip()
    if existing & target_names or containers:
        raise RuntimeError(
            "Restore project already exists; destroy that disposable project explicitly before retrying"
        )
    original_stage = controller.STAGE
    restored_stage = controller.WORK / "restored-repo"
    if restored_stage.exists() or restored_stage.is_symlink():
        raise RuntimeError(
            "Restored configuration already exists; preserve it before retrying recovery"
        )
    if not {f"{PROJECT}_{name}" for name in VOLUMES} <= existing:
        raise RuntimeError(
            "Rehearsal source volumes are incomplete; refusing to archive partial state"
        )
    identity = controller.identity()
    marker = controller.inside(
        "agent", ["cat", "/tmp/openvox-rehearsal-marker"]
    ).strip()
    private = controller.WORK / "private"
    private.mkdir(mode=0o700, exist_ok=True)
    private.chmod(0o700)
    directory = Path(tempfile.mkdtemp(prefix="recovery-", dir=private))
    controller.compose("--profile", "ceremony", "--profile", "tools", "stop")
    manifest = {"source_project": PROJECT, "restore_project": RESTORE, "volumes": {}}
    configuration_archive = directory / "configuration.tar.gz"
    _save_configuration(original_stage, configuration_archive)
    manifest["configuration"] = _digest(configuration_archive)
    for name in VOLUMES:
        archive = directory / (name + ".tar.gz")
        _transfer(
            _volume_command(controller, f"{PROJECT}_{name}", restore=False), archive
        )
        manifest["volumes"][name] = _digest(archive)
    restored_bytes = 0
    for archive in directory.glob("*.tar.gz"):
        with tarfile.open(archive, "r|gz") as contents:
            restored_bytes += sum(
                ((member.size + 4095) // 4096 + 1) * 4096 for member in contents
            )
    image_bytes = int(
        controller.execute(
            controller.DOCKER + ["image", "inspect", TOOLS, "--format", "{{.Size}}"]
        ).strip()
    )
    # Image loading may temporarily unpack every layer alongside the compressed backup.
    required_bytes = image_bytes * 2 + restored_bytes + 256 * 1024 * 1024
    if shutil.disk_usage(directory).free < required_bytes:
        raise RuntimeError(
            f"Recovery needs at least {required_bytes // (1024 * 1024)} MiB free for image backup, loading and separate volumes"
        )
    image_archive = directory / "images.tar.gz"
    _save_images(controller, image_archive)
    manifest["images"] = _digest(image_archive)
    manifest["archive_directory"] = str(directory)
    manifest_path = directory / "manifest.json"
    with os.fdopen(
        os.open(manifest_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
    ) as output:
        json.dump(manifest, output, indent=2)
        output.write("\n")
    controller.check(
        "all disposable volumes, configuration and exact images archived with private permissions"
    )
    _transfer(controller.DOCKER + ["image", "load"], image_archive, restore=True)
    _restore_configuration(configuration_archive, restored_stage)
    for name in VOLUMES:
        volume = f"{RESTORE}_{name}"
        controller.execute(
            controller.DOCKER
            + [
                "volume",
                "create",
                "--label",
                f"com.docker.compose.project={RESTORE}",
                "--label",
                f"com.docker.compose.volume={name}",
                volume,
            ]
        )
        archive = directory / manifest["volumes"][name]["file"]
        if _digest(archive) != manifest["volumes"][name]:
            raise RuntimeError(
                "Recovery archive changed after backup; refusing restore"
            )
        _transfer(
            _volume_command(controller, volume, restore=True), archive, restore=True
        )
    controller.PROJECT = RESTORE
    controller.STAGE = restored_stage
    try:
        try:
            controller.start_vault("root")
            certificate = controller.api("root", "GET", "pki/cert/ca")["data"][
                "certificate"
            ]
            fingerprint = hashlib.sha256(
                ssl.PEM_cert_to_DER_cert(certificate)
            ).hexdigest()
            controller.check(
                "independent restore preserves offline root identity",
                fingerprint == controller.REPORT["root_sha256"],
            )
        finally:
            controller.compose("stop", "root")
        controller.start_vault("vault")
        controller.compose(
            "up",
            "-d",
            "--no-deps",
            "--wait",
            "--wait-timeout",
            "240",
            "openvox",
            timeout=300,
        )
        controller.compose("up", "-d", "--no-deps", "agent")
        controller.check(
            "independent restore preserves OpenVox CA and server identities",
            controller.identity() == identity,
        )
        controller.check(
            "restored native agent applies a catalog", controller.agent_run() in (0, 2)
        )
        controller.check(
            "restored Vault Hiera value matches original",
            controller.inside("agent", ["cat", "/tmp/openvox-rehearsal-marker"]).strip()
            == marker,
        )
        controller.check("restored native agent converges", controller.agent_run() == 0)
        state = json.loads(
            controller.inside(
                "openvox", [controller.RUBY, "/rehearsal/verify-state.rb"]
            )
        )
        controller.check(
            "restored runtime seed encodings, chain and CRLs verified", state["ok"]
        )
        acceptance = json.loads(
            controller.inside("agent", [controller.RUBY, "/rehearsal/acceptance.rb"])
        )
        functional = acceptance["checks"]
        controller.check(
            "restored TLS and authorization acceptance repeated",
            bool(functional) and all(functional.values()),
        )
        manifest["restored_runtime_state"] = state
        manifest["restored_acceptance"] = acceptance
        controller.outbound_acceptance()
        manifest["restored_outbound"] = json.loads(
            (controller.WORK / "outbound-transports.json").read_text()
        )
        controller.check("restored Python and JRuby outbound TLS acceptance repeated")
    finally:
        controller.PROJECT = PROJECT
        controller.STAGE = original_stage
    return manifest


def _self_check() -> None:
    """Verify binary transfer, private permissions and redacted failure diagnostics."""
    with tempfile.TemporaryDirectory() as temporary:
        archive = Path(temporary) / "binary.tar.gz"
        _transfer(
            [
                sys.executable,
                "-c",
                "import sys;sys.stdout.buffer.write(bytes(range(256)))",
            ],
            archive,
        )
        assert archive.read_bytes() == bytes(range(256))
        assert archive.stat().st_mode & 0o777 == 0o600
        assert (
            _digest(archive)["sha256"] == hashlib.sha256(bytes(range(256))).hexdigest()
        )
        _transfer(
            [
                sys.executable,
                "-c",
                "import sys;assert sys.stdin.buffer.read()==bytes(range(256))",
            ],
            archive,
            restore=True,
        )
        try:
            _transfer(
                [
                    sys.executable,
                    "-c",
                    "import sys;sys.stderr.write('private-token');sys.exit(3)",
                ],
                Path(temporary) / "failed",
            )
        except RuntimeError as error:
            assert "private-token" not in str(error)
            log = Path(temporary) / "failed.failure.log"
            assert log.read_bytes() == b"private-token"
            assert log.stat().st_mode & 0o777 == 0o600
        else:
            raise AssertionError("Failed archive command was accepted")
        source = Path(temporary) / "configuration"
        source.mkdir(mode=0o700)
        config = source / "compose.yaml"
        config.write_text("# Zürich\nservices: {}\n")
        config.chmod(0o640)
        helper = source / "helper.sh"
        helper.write_text("#!/bin/sh\nexit 0\n")
        helper.chmod(0o750)
        config_archive = Path(temporary) / "configuration.tar.gz"
        _save_configuration(source, config_archive)
        restored = Path(temporary) / "independent-configuration"
        _restore_configuration(config_archive, restored)
        assert config_archive.stat().st_mode & 0o777 == 0o600
        assert (restored / "compose.yaml").read_bytes() == config.read_bytes()
        assert (restored / "compose.yaml").stat().st_mode & 0o777 == 0o644
        assert (restored / "helper.sh").stat().st_mode & 0o777 == 0o755
        assert restored.stat().st_mode & 0o777 == 0o755
        assert source.stat().st_mode & 0o777 == 0o700
        assert config.stat().st_mode & 0o777 == 0o640
        assert helper.stat().st_mode & 0o777 == 0o750
    print(
        "Recovery binary transfer, independent configuration and privacy self-check passed"
    )


if __name__ == "__main__":
    _self_check()
