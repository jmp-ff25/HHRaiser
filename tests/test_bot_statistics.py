from __future__ import annotations

import json
import sqlite3
import unittest
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from hh_raiser.bot.models import InstanceStatistics, ManagedInstance, ServiceSnapshot
from hh_raiser.bot.presentation import (
    format_logs,
    format_periodic_summary,
    format_statistics,
)
from hh_raiser.bot.statistics import read_instance_statistics, sent_counts_by_moscow_day
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
        self.assertEqual(statistics.average_lexical_score, 72)
        self.assertEqual(statistics.responses_by_status["sent"], 1)
        self.assertEqual(statistics.today_sent, 1)
        self.assertEqual(statistics.next_raise_at, next_raise)

    def test_missing_database_produces_empty_statistics(self) -> None:
        with TemporaryDirectory() as directory:
            statistics = read_instance_statistics(Path(directory))

        self.assertEqual(statistics.discovered, 0)
        self.assertEqual(statistics.responses_by_status, {})

    def test_reads_legacy_history_before_worker_migrates_it(self) -> None:
        with TemporaryDirectory() as directory:
            state_dir = Path(directory)
            with closing(sqlite3.connect(state_dir / "vacancy-history.sqlite3")) as connection, connection:
                connection.executescript(
                    """
                    CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    INSERT INTO metadata VALUES ('current_generation', '1');
                    CREATE TABLE vacancies(
                        vacancy_id TEXT PRIMARY KEY, last_viewed_at TEXT,
                        view_count INTEGER, last_evaluated_at TEXT, last_match_score INTEGER
                    );
                    INSERT INTO vacancies VALUES ('123', NULL, 0, '2026-09-26', 42);
                    CREATE TABLE vacancy_responses(status TEXT, occurred_at TEXT);
                    """
                )
            statistics = read_instance_statistics(state_dir)
        self.assertEqual(statistics.evaluated, 1)
        self.assertEqual(statistics.average_lexical_score, 42)
        self.assertEqual(statistics.semantic_verdicts, {})

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

    def test_presentation_distinguishes_response_outcomes(self) -> None:
        instance = ManagedInstance(
            key="main",
            name="Основное резюме",
            service_name="hhraiser@main.service",
            state_dir=Path("state"),
            config_file=Path("state/hh-config.ini"),
        )
        statistics = InstanceStatistics(
            generation=1,
            discovered=10,
            viewed_vacancies=5,
            total_views=5,
            evaluated=5,
            average_lexical_score=70,
            responses_by_status=Counter({"sent": 1, "already_sent": 5, "manual_required": 1}),
            next_raise_at=None,
            today_sent=1,
            daily_limit=15,
            responses_enabled=True,
            matching_mode="semantic",
            semantic_verdicts={"fit": 2, "unfit": 3},
        )

        text = format_statistics(instance, statistics)
        summary = format_periodic_summary(
            instance,
            ServiceSnapshot(active_state="active", sub_state="running", main_pid=12),
            statistics,
        )

        self.assertIn("Учтено исходов обработки вакансий: <b>7</b>", text)
        self.assertIn("Средняя лексическая оценка, справочно: <b>70.0%</b>", text)
        self.assertIn("подходит: 2", text)
        self.assertIn("Подтверждённые отклики сегодня (Москва): 1 из 15", text)
        self.assertIn("отклик уже существовал до обработки HHRaiser: 5", text)
        self.assertIn("подтверждено сегодня: 1 из 15", summary)
        self.assertIn("отправлено HHRaiser за всё время: 1", summary)
        self.assertIn("уже были отправлены: 5", summary)
        self.assertIn("требуют вашего участия: 1", summary)

    def test_moscow_daily_count_ignores_non_sends_and_converts_utc(self) -> None:
        rows = [
            ("sent", "2026-09-26T20:59:00+00:00"),
            ("sent", "2026-09-26T21:00:00+00:00"),
            ("sent", "2026-09-27T20:59:00+00:00"),
            ("sent", "2026-09-27T21:00:00+00:00"),
            ("already_sent", "2026-09-27T10:00:00+03:00"),
            ("manual_required", "2026-09-27T11:00:00+03:00"),
        ]
        counts = sent_counts_by_moscow_day(rows)
        self.assertEqual(counts["2026-09-27"], 2)
        self.assertEqual(counts["2026-09-28"], 1)

    def test_reads_semantic_verdicts_and_today_target_from_config(self) -> None:
        with TemporaryDirectory() as directory:
            state_dir = Path(directory)
            history = VacancyHistory(state_dir / "vacancy-history.sqlite3")
            url = "https://hh.ru/vacancy/123"
            history.reserve_unseen(urls=[url], search_query="Python", limit=1, revisit_after_days=0)
            history.mark_evaluated(
                url, score=20, accepted=True, mode="semantic", semantic_verdict="fit"
            )
            history.record_response(
                VacancyResponseRecord(
                    vacancy_id="123",
                    occurred_at=datetime(2026, 9, 26, 21, 30, tzinfo=UTC),
                    status=VacancyResponseStatus.SENT,
                    detail="Отправлен",
                    vacancy_title="RAG developer",
                    company_name="Example",
                    search_query="Python",
                    match_score=20,
                )
            )
            config = state_dir / "hh-config.ini"
            config.write_text(
                "[matching]\nmode = semantic\n[responses]\nenabled = true\ndaily_limit = 25\n",
                encoding="utf-8",
            )
            statistics = read_instance_statistics(
                state_dir,
                config_file=config,
                now=datetime(2026, 9, 27, 12, tzinfo=UTC),
            )
        self.assertEqual(statistics.today_sent, 1)
        self.assertEqual(statistics.daily_limit, 25)
        self.assertEqual(statistics.semantic_verdicts, {"fit": 1})
        self.assertEqual(statistics.average_lexical_score, 20)
