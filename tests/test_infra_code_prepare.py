"""Verify first-deployment code preparation preserves existing environments."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "docker/openvox/infra-code-prepare.sh"


@pytest.mark.parametrize("kind", ["empty", "template", "git", "unknown", "preserved"])
def test_prepare_code(tmp_path: Path, kind: str) -> None:
    """Move only an exact packaged template, retaining all unknown or Git code."""
    packaged = tmp_path / "packaged"
    production = packaged / "environments/production"
    production.mkdir(parents=True)
    (production / "hiera.yaml").write_text("packaged template")
    code = tmp_path / "code"
    if kind == "empty":
        code.mkdir()
    else:
        shutil.copytree(packaged, code)
    active = code / "environments/production"
    if kind == "git":
        (active / ".git").mkdir()
        (active / "hiera.yaml").write_text("deployed Git code")
    elif kind == "unknown":
        (active / "hiera.yaml").write_text("operator changes")
    elif kind == "preserved":
        (code / "packaged-environments").mkdir()
    before = {
        str(p.relative_to(code)): p.read_bytes() for p in code.rglob("*") if p.is_file()
    }
    binary = tmp_path / "bin"
    binary.mkdir()
    install = binary / "install"
    install.write_text('#!/bin/bash\nexec /usr/bin/install -d -m 0755 "${@: -1}"\n')
    install.chmod(0o755)
    result = subprocess.run(
        ["bash", str(SCRIPT), str(code), str(packaged)],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{binary}:{os.environ['PATH']}"},
        check=False,
    )
    assert (result.returncode == 0) == (kind not in {"unknown", "preserved"})
    if kind == "template":
        assert (
            code / "packaged-environments/production/hiera.yaml"
        ).read_text() == "packaged template"
        assert not list((code / "environments").iterdir())
        repeated = subprocess.run(
            ["bash", str(SCRIPT), str(code), str(packaged)], check=False
        )
        assert repeated.returncode == 0
    else:
        assert {
            str(p.relative_to(code)): p.read_bytes()
            for p in code.rglob("*")
            if p.is_file()
        } == before
