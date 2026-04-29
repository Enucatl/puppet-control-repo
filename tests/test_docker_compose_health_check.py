from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


FILES_DIR = Path(__file__).parents[1] / "modules" / "profile" / "files"
SPEC = importlib.util.spec_from_file_location(
    "docker_compose_health_check", FILES_DIR / "docker_compose_health_check.py"
)
assert SPEC is not None
health_check = importlib.util.module_from_spec(SPEC)
sys.modules["docker_compose_health_check"] = health_check
assert SPEC.loader is not None
SPEC.loader.exec_module(health_check)


class TestDockerComposeHealthCheck:
    def test_accepts_json_array_with_running_and_clean_exited(self) -> None:
        containers = health_check.parse_ps_output(
            """
            [
              {"Name": "web", "State": "running", "Health": "healthy"},
              {"Name": "migrate", "State": "exited", "ExitCode": 0}
            ]
            """
        )

        assert all(health_check.is_acceptable(item) for item in containers)

    def test_accepts_json_lines(self) -> None:
        containers = health_check.parse_ps_output(
            '{"Name":"web","State":"running"}\n'
            '{"Name":"migrate","State":"exited","ExitCode":"0"}\n'
        )

        assert [item["Name"] for item in containers] == ["web", "migrate"]
        assert all(health_check.is_acceptable(item) for item in containers)

    def test_rejects_unhealthy_running_container(self) -> None:
        container = {"Name": "web", "State": "running", "Health": "unhealthy"}

        assert not health_check.is_acceptable(container)

    def test_rejects_nonzero_exited_container(self) -> None:
        container = {"Name": "migrate", "State": "exited", "ExitCode": "1"}

        assert not health_check.is_acceptable(container)

    def test_main_appends_ps_to_compose_command(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = subprocess.CompletedProcess(
            ["docker"],
            0,
            '[{"Name":"web","State":"running","Health":"healthy"}]',
            "",
        )
        calls: list[tuple[object, ...]] = []

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess:
            calls.append((*args, kwargs))
            return result

        monkeypatch.setattr(health_check.subprocess, "run", fake_run)

        exit_code = health_check.main(["/usr/bin/docker", "compose", "-f", "app.yml"])

        assert exit_code == 0
        assert calls == [
            (
                [
                    "/usr/bin/docker",
                    "compose",
                    "-f",
                    "app.yml",
                    "ps",
                    "--all",
                    "--format",
                    "json",
                ],
                {
                    "check": True,
                    "capture_output": True,
                    "text": True,
                },
            )
        ]
