from __future__ import annotations

import unittest
from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from hh_raiser.domain.vacancy_response import VacancyResponseRecord, VacancyResponseStatus
from hh_raiser.infrastructure.storage.vacancy_history import VacancyHistory
from hh_raiser.management import ProjectLayout, SetupError, main, validate_setup
from hh_raiser.models import MOSCOW


class ManagementTests(unittest.TestCase):
    def test_response_report_returns_failure_until_target_is_met(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pyproject.toml").write_text("", encoding="utf-8")
            history = VacancyHistory(root / "state" / "main" / "vacancy-history.sqlite3")
            history.record_response(
                VacancyResponseRecord(
                    vacancy_id="123",
                    occurred_at=datetime(2026, 9, 27, 12, tzinfo=MOSCOW),
                    status=VacancyResponseStatus.SENT,
                    detail="confirmed",
                    vacancy_title="Backend",
                    company_name="Example",
                    search_query="Python",
                    match_score=80,
                )
            )
            output = StringIO()
            with redirect_stdout(output):
                unmet = main(
                    [
                        "--project-dir",
                        str(root),
                        "responses",
                        "--date",
                        "2026-09-27",
                        "--target",
                        "2",
                    ]
                )
                met = main(
                    [
                        "--project-dir",
                        str(root),
                        "responses",
                        "--date",
                        "2026-09-27",
                        "--target",
                        "1",
                    ]
                )
            self.assertEqual(unmet, 1)
            self.assertEqual(met, 0)
            self.assertIn("подтверждено новых откликов — 1", output.getvalue())

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
