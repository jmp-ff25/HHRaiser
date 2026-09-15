from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from hh_raiser.bot.charts import ChartReadError, render_statistics_dashboard
from hh_raiser.domain.vacancy_response import VacancyResponseRecord, VacancyResponseStatus
from hh_raiser.infrastructure.storage.vacancy_history import VacancyHistory


class BotChartTests(unittest.TestCase):
    def test_renders_png_dashboard_from_history(self) -> None:
        with TemporaryDirectory() as directory:
            state_dir = Path(directory)
            history = VacancyHistory(state_dir / "vacancy-history.sqlite3")
            url = "https://hh.ru/vacancy/123"
            history.reserve_unseen(
                [url],
                search_query="Backend Python",
                limit=1,
                revisit_after_days=0,
            )
            history.mark_evaluated(url, score=78, accepted=True)
            history.mark_viewed(url)
            history.record_response(
                VacancyResponseRecord.now(
                    vacancy_id="123",
                    status=VacancyResponseStatus.SENT,
                    detail="Отправлен",
                    vacancy_title="Backend developer",
                    company_name="Example",
                    search_query="Backend Python",
                    match_score=78,
                )
            )

            image = render_statistics_dashboard(state_dir, instance_name="Основное резюме")

        self.assertTrue(image.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(image), 10_000)

    def test_missing_history_has_readable_error(self) -> None:
        with (
            TemporaryDirectory() as directory,
            self.assertRaisesRegex(ChartReadError, "История вакансий"),
        ):
            render_statistics_dashboard(Path(directory), instance_name="Основное")


if __name__ == "__main__":
    unittest.main()
