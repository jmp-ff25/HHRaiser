from __future__ import annotations

import json
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from hh_raiser.bot.models import ManagedInstance, ServiceSnapshot
from hh_raiser.bot.presentation import (
    format_logs,
    format_periodic_summary,
    format_statistics,
)
from hh_raiser.bot.statistics import read_instance_statistics
from hh_raiser.domain.vacancy_response import VacancyResponseRecord, VacancyResponseStatus
from hh_raiser.infrastructure.storage.vacancy_history import VacancyHistory
from hh_raiser.models import MOSCOW


class BotStatisticsTests(unittest.TestCase):
    def test_reads_aggregate_statistics_from_existing_state(self) -> None:
        with TemporaryDirectory() as directory:
            state_dir = Path(directory)
            history = VacancyHistory(state_dir / "vacancy-history.sqlite3")
            url = "https://hh.ru/vacancy/123"
            history.reserve_unseen(
                [url],
                search_query="Python",
                limit=1,
                revisit_after_days=0,
            )
            history.mark_evaluated(url, score=72, accepted=True)
            history.mark_viewed(url)
            history.record_response(
                VacancyResponseRecord.now(
                    vacancy_id="123",
                    status=VacancyResponseStatus.SENT,
                    detail="Отправлен",
                    vacancy_title="Backend developer",
                    company_name="Example",
                    search_query="Python",
                    match_score=72,
                )
            )
            next_raise = datetime(2026, 9, 12, 18, 30, tzinfo=MOSCOW)
            (state_dir / "status.json").write_text(
                json.dumps({"next_raise_at": next_raise.isoformat()}),
                encoding="utf-8",
            )

            statistics = read_instance_statistics(state_dir)

        self.assertEqual(statistics.discovered, 1)
        self.assertEqual(statistics.viewed_vacancies, 1)
        self.assertEqual(statistics.total_views, 1)
        self.assertEqual(statistics.evaluated, 1)
        self.assertEqual(statistics.average_match_score, 72)
        self.assertEqual(statistics.responses_by_status["sent"], 1)
        self.assertEqual(statistics.next_raise_at, next_raise)

    def test_missing_database_produces_empty_statistics(self) -> None:
        with TemporaryDirectory() as directory:
            statistics = read_instance_statistics(Path(directory))

        self.assertEqual(statistics.discovered, 0)
        self.assertEqual(statistics.responses_by_status, {})

    def test_presentation_explains_counters_and_escapes_logs(self) -> None:
        with TemporaryDirectory() as directory:
            statistics = read_instance_statistics(Path(directory))
        instance = ManagedInstance(
            key="main",
            name="Основное <резюме>",
            service_name="hhraiser@main.service",
            state_dir=Path("state"),
            config_file=Path("state/hh-config.ini"),
        )

        text = format_statistics(instance, statistics)
        logs = format_logs("message <token>")

        self.assertIn("Найдено уникальных вакансий", text)
        self.assertIn("Основное &lt;резюме&gt;", text)
        self.assertIn("message &lt;token&gt;", logs)

        summary = format_periodic_summary(
            instance,
            ServiceSnapshot(active_state="active", sub_state="running", main_pid=12),
            statistics,
        )
        self.assertIn("Работает", summary)
        self.assertIn("Основное &lt;резюме&gt;", summary)
