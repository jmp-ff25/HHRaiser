from __future__ import annotations

import unittest
from pathlib import Path


class TaskfileTests(unittest.TestCase):
    def test_launch_profiles_do_not_override_configurable_numbers(self) -> None:
        taskfile = (Path(__file__).parents[1] / "Taskfile.yml").read_text(
            encoding="utf-8"
        )

        for option in (
            "--match-threshold",
            "--activity-interval-seconds",
            "--resume-index-refresh-seconds",
        ):
            with self.subTest(option=option):
                self.assertNotIn(option, taskfile)
