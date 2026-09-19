from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from hh_raiser.management import ProjectLayout, SetupError, validate_setup


class ManagementTests(unittest.TestCase):
    def test_accepts_complete_project_configuration(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            layout = ProjectLayout(root)
            layout.config_file.parent.mkdir(parents=True)
            layout.config_file.write_text(
                "[resume]\ntitle = Резюме\n[activity]\nsearch_queries = Python\n",
                encoding="utf-8",
            )
            layout.env_file.write_text(
                "HH_PHONE=79990000000\n"
                "HH_PASSWORD=password\n"
                "HHRAISER_BOT_TOKEN=token\n"
                "HHRAISER_BOT_ALLOWED_USER_IDS=1\n"
                "HHRAISER_BOT_INSTANCES=main\n"
                "HHRAISER_INSTANCE_MAIN_SERVICE=hhraiser@main.service\n"
                "HHRAISER_INSTANCE_MAIN_STATE_DIR=state/main\n",
                encoding="utf-8",
            )

            validate_setup(layout, environment={})

    def test_reports_missing_secret_without_prompting(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            layout = ProjectLayout(root)
            layout.config_file.parent.mkdir(parents=True)
            layout.config_file.write_text(
                "[resume]\ntitle = Резюме\n[activity]\nsearch_queries = Python\n",
                encoding="utf-8",
            )
            layout.env_file.write_text("HH_PHONE=79990000000\n", encoding="utf-8")

            with self.assertRaisesRegex(SetupError, "HH_PASSWORD"):
                validate_setup(layout, environment={})

    def test_reports_missing_configuration_files(self) -> None:
        with TemporaryDirectory() as directory, self.assertRaisesRegex(SetupError, ".env"):
            validate_setup(ProjectLayout(Path(directory)), environment={})
