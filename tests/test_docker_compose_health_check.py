from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock


FILES_DIR = Path(__file__).parents[1] / "modules" / "profile" / "files"
SPEC = importlib.util.spec_from_file_location(
    "docker_compose_health_check", FILES_DIR / "docker_compose_health_check.py"
)
assert SPEC is not None
health_check = importlib.util.module_from_spec(SPEC)
sys.modules["docker_compose_health_check"] = health_check
assert SPEC.loader is not None
SPEC.loader.exec_module(health_check)


class DockerComposeHealthCheckTest(unittest.TestCase):
    def test_accepts_json_array_with_running_and_clean_exited(self) -> None:
        containers = health_check.parse_ps_output(
            """
            [
              {"Name": "web", "State": "running", "Health": "healthy"},
              {"Name": "migrate", "State": "exited", "ExitCode": 0}
            ]
            """
        )

        self.assertTrue(all(health_check.is_acceptable(item) for item in containers))

    def test_accepts_json_lines(self) -> None:
        containers = health_check.parse_ps_output(
            '{"Name":"web","State":"running"}\n'
            '{"Name":"migrate","State":"exited","ExitCode":"0"}\n'
        )

        self.assertEqual([item["Name"] for item in containers], ["web", "migrate"])
        self.assertTrue(all(health_check.is_acceptable(item) for item in containers))

    def test_rejects_unhealthy_running_container(self) -> None:
        container = {"Name": "web", "State": "running", "Health": "unhealthy"}

        self.assertFalse(health_check.is_acceptable(container))

    def test_rejects_nonzero_exited_container(self) -> None:
        container = {"Name": "migrate", "State": "exited", "ExitCode": "1"}

        self.assertFalse(health_check.is_acceptable(container))

    def test_main_appends_ps_to_compose_command(self) -> None:
        result = subprocess.CompletedProcess(
            ["docker"],
            0,
            '[{"Name":"web","State":"running","Health":"healthy"}]',
            "",
        )

        with mock.patch.object(
            health_check.subprocess, "run", return_value=result
        ) as run:
            exit_code = health_check.main(
                ["/usr/bin/docker", "compose", "-f", "app.yml"]
            )

        self.assertEqual(exit_code, 0)
        run.assert_called_once_with(
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
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
