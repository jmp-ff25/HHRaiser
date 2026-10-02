from __future__ import annotations

import random
import sqlite3
import unittest
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from hh_raiser.domain.matching import ModelDecision
from hh_raiser.domain.vacancy_response import (
    ManualResponseReason,
    VacancyResponseRecord,
    VacancyResponseStatus,
)
from hh_raiser.infrastructure.storage.vacancy_history import (
    VacancyHistory,
    vacancy_id_from_url,
)
from hh_raiser.models import MOSCOW
from hh_raiser.storage import (
    read_next_raise_time,
    write_next_raise_time,
    write_resume_refresh_attempt,
)


class StorageTests(unittest.TestCase):
    def test_next_raise_time_is_persisted(self) -> None:
        next_at = datetime(2026, 8, 27, 4, 34, tzinfo=MOSCOW)
        with TemporaryDirectory() as directory:
            profile_dir = Path(directory) / "browser-profile"
            write_next_raise_time(profile_dir, next_at)
            self.assertEqual(read_next_raise_time(profile_dir), next_at)
            self.assertTrue((profile_dir.parent / "status.json").exists())

    def test_resume_refresh_attempt_is_persisted_without_resume_text(self) -> None:
        attempted_at = datetime.now(MOSCOW)
        with TemporaryDirectory() as directory:
            profile_dir = Path(directory) / "browser-profile"
            write_resume_refresh_attempt(profile_dir, attempted_at)

            payload = (profile_dir / "resume-refresh-last-attempt.json").read_text(encoding="utf-8")

            self.assertIn(attempted_at.isoformat(), payload)
            self.assertNotIn("description", payload)


class VacancyHistoryTests(unittest.TestCase):
    def test_paid_fit_waits_for_response_until_hh_confirms_or_requires_form(self) -> None:
        for status in (
            VacancyResponseStatus.SENT,
            VacancyResponseStatus.ALREADY_SENT,
            VacancyResponseStatus.MANUAL_REQUIRED,
        ):
            with self.subTest(status=status), TemporaryDirectory() as directory:
                history = VacancyHistory(Path(directory) / "history.sqlite3")
                url = "https://hh.ru/vacancy/123"
                fingerprint = "selected-resume"
                history.record_model_evaluation(
                    url,
                    resume_fingerprint=fingerprint,
                    model="deepseek",
                    decision=ModelDecision("fit", "Подходит", ()),
                    pending_response=True,
                )
                self.assertTrue(history.pending_model_response(url, fingerprint))

                history.record_response(
                    VacancyResponseRecord.now(
                        vacancy_id="123",
                        status=status,
                        detail="HH подтвердил результат",
                        vacancy_title="Python",
                        company_name="Example",
                        search_query="Python",
                        match_score=None,
                    )
                )

                self.assertFalse(history.pending_model_response(url, fingerprint))
                self.assertEqual(history.response_status(url), status)
                with closing(sqlite3.connect(history.path)) as connection:
                    row = connection.execute(
                        "SELECT response_state FROM vacancy_model_evaluations"
                    ).fetchone()
                self.assertEqual(row, ("resolved",))

    def test_old_paid_fit_remains_review_only_after_schema_migration(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.executescript(
                    """CREATE TABLE vacancy_model_evaluations (
                        vacancy_id TEXT NOT NULL,
                        resume_fingerprint TEXT NOT NULL,
                        evaluated_at TEXT NOT NULL,
                        model TEXT NOT NULL,
                        verdict TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        gaps_json TEXT NOT NULL,
                        input_tokens INTEGER NOT NULL DEFAULT 0,
                        output_tokens INTEGER NOT NULL DEFAULT 0,
                        cost_rub REAL,
                        PRIMARY KEY (vacancy_id, resume_fingerprint)
                    );
                    INSERT INTO vacancy_model_evaluations
                    (vacancy_id, resume_fingerprint, evaluated_at, model, verdict, reason,
                     gaps_json)
                    VALUES ('123', 'selected-resume', '2026-10-02T10:00:00+03:00',
                            'deepseek', 'fit', 'Подходит', '[]');"""
                )

            history = VacancyHistory(path)

            self.assertEqual(
                history.model_evaluation("https://hh.ru/vacancy/123", "selected-resume").verdict,
                "fit",
            )
            self.assertFalse(
                history.pending_model_response("https://hh.ru/vacancy/123", "selected-resume")
            )

    def test_persists_search_page_coverage_and_selection_counters(self) -> None:
        with TemporaryDirectory() as directory:
            history = VacancyHistory(Path(directory) / "history.sqlite3")
            reservation = history.reserve_candidates(
                [
                    "https://hh.ru/vacancy/101",
                    "https://hh.ru/vacancy/102",
                ],
                search_query="Backend",
                limit=1,
                revisit_after_days=14,
            )
            history.record_search_page(
                search_query="Backend",
                page=3,
                page_count=8,
                reservation=reservation,
            )

            restored = history.search_coverage(("Backend",))

        self.assertEqual(reservation.discovered_count, 2)
        self.assertEqual(reservation.newly_discovered_count, 2)
        self.assertEqual(reservation.eligible_count, 2)
        self.assertEqual(len(reservation.urls), 1)
        self.assertEqual(restored, {"Backend": (8, frozenset({3}))})

    def test_new_generation_does_not_restore_previous_page_coverage(self) -> None:
        with TemporaryDirectory() as directory:
            history = VacancyHistory(Path(directory) / "history.sqlite3")
            reservation = history.reserve_candidates(
                ["https://hh.ru/vacancy/201"],
                search_query="Python",
                limit=1,
                revisit_after_days=0,
            )
            history.record_search_page(
                search_query="Python",
                page=0,
                page_count=2,
                reservation=reservation,
            )
            history.advance_generation()

            restored = history.search_coverage(("Python",))

        self.assertEqual(restored, {})

    def test_existing_database_is_migrated_without_deleting_history(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "vacancy-history.sqlite3"
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.executescript(
                    """
                    CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    INSERT INTO metadata(key, value) VALUES ('current_generation', '1');
                    CREATE TABLE vacancies (
                        vacancy_id TEXT PRIMARY KEY,
                        first_seen_at TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL,
                        last_viewed_at TEXT,
                        view_count INTEGER NOT NULL DEFAULT 0,
                        last_viewed_generation INTEGER,
                        reserved_generation INTEGER,
                        reserved_until TEXT
                    );
                    INSERT INTO vacancies(vacancy_id, first_seen_at, last_seen_at)
                    VALUES ('123', '2026-09-01', '2026-09-01');
                    CREATE TABLE vacancy_responses (
                        vacancy_id TEXT PRIMARY KEY,
                        occurred_at TEXT NOT NULL,
                        status TEXT NOT NULL,
                        detail TEXT NOT NULL,
                        vacancy_title TEXT NOT NULL,
                        company_name TEXT NOT NULL,
                        search_query TEXT NOT NULL,
                        match_score INTEGER,
                        manual_reason TEXT
                    );
                    INSERT INTO vacancy_responses(
                        vacancy_id, occurred_at, status, detail, vacancy_title,
                        company_name, search_query, match_score, manual_reason
                    ) VALUES (
                        '123', '2026-09-01T10:00:00+03:00', 'sent', 'Подтверждено.',
                        'Python developer', 'Example', 'Python', 80, NULL
                    );
                    """
                )

            history = VacancyHistory(path)

            with closing(sqlite3.connect(path)) as connection, connection:
                columns = {
                    str(row[1]) for row in connection.execute("PRAGMA table_info(vacancies)")
                }
                response_columns = {
                    str(row[1])
                    for row in connection.execute("PRAGMA table_info(vacancy_responses)")
                }
                row = connection.execute(
                    "SELECT vacancy_id FROM vacancies WHERE vacancy_id = '123'"
                ).fetchone()
            self.assertIn("last_match_score", columns)
            self.assertIn("last_match_accepted", columns)
            self.assertIn("last_matching_mode", columns)
            self.assertIn("last_semantic_verdict", columns)
            self.assertIn("post_response_modal_text", response_columns)
            self.assertEqual(row, ("123",))
            self.assertEqual(history.response_records()[0].detail, "Подтверждено.")

    def test_extracts_only_canonical_vacancy_identifier(self) -> None:
        self.assertEqual(vacancy_id_from_url("https://hh.ru/vacancy/123"), "123")
        self.assertIsNone(vacancy_id_from_url("https://hh.ru/search/vacancy?page=1"))
        self.assertIsNone(vacancy_id_from_url("https://example.com/vacancy/123"))

    def test_persists_viewed_vacancy_across_instances_and_queries(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "vacancy-history.sqlite3"
            first = VacancyHistory(path, randomizer=random.Random(1))
            url = "https://hh.ru/vacancy/123"
            self.assertEqual(
                first.reserve_unseen([url], search_query="Python", limit=1, revisit_after_days=0),
                [url],
            )
            first.mark_viewed(url)

            second = VacancyHistory(path, randomizer=random.Random(1))
            selected = second.reserve_unseen(
                [url], search_query="Backend", limit=1, revisit_after_days=0
            )

            self.assertEqual(selected, [])
            self.assertEqual(second.viewed_count(), 1)

    def test_new_generation_allows_old_vacancy_when_revisit_delay_is_disabled(self) -> None:
        with TemporaryDirectory() as directory:
            history = VacancyHistory(
                Path(directory) / "vacancy-history.sqlite3",
                randomizer=random.Random(1),
            )
            url = "https://hh.ru/vacancy/123"
            history.reserve_unseen([url], search_query="Python", limit=1, revisit_after_days=0)
            history.mark_viewed(url)

            history.advance_generation()

            self.assertEqual(
                history.reserve_unseen(
                    [url], search_query="Python", limit=1, revisit_after_days=14
                ),
                [],
            )
            self.assertEqual(
                history.reserve_unseen([url], search_query="Python", limit=1, revisit_after_days=0),
                [url],
            )

    def test_release_makes_failed_reservation_available_again(self) -> None:
        with TemporaryDirectory() as directory:
            history = VacancyHistory(
                Path(directory) / "vacancy-history.sqlite3",
                randomizer=random.Random(1),
            )
            url = "https://hh.ru/vacancy/123"
            history.reserve_unseen([url], search_query="Python", limit=1, revisit_after_days=0)

            history.release(url)

            self.assertEqual(
                history.reserve_unseen([url], search_query="Python", limit=1, revisit_after_days=0),
                [url],
            )

    def test_response_outcome_is_persisted_once_and_blocks_automatic_retry(self) -> None:
        with TemporaryDirectory() as directory:
            history = VacancyHistory(Path(directory) / "history.sqlite3")
            url = "https://hh.ru/vacancy/789"
            history.reserve_unseen([url], search_query="Python", limit=1, revisit_after_days=0)
            record = VacancyResponseRecord.now(
                vacancy_id="789",
                status=VacancyResponseStatus.MANUAL_REQUIRED,
                detail="Нужна анкета.",
                vacancy_title="Python developer",
                company_name="Example",
                search_query="Python",
                match_score=75,
                manual_reason=ManualResponseReason.QUESTIONNAIRE,
                post_response_modal_text="HH показал информационное окно.",
            )

            self.assertTrue(history.record_response(record))
            self.assertFalse(history.record_response(record))
            self.assertTrue(history.has_response_record(url))
            self.assertEqual(history.response_records(), [record])

    def test_counts_only_successful_responses_from_current_moscow_day(self) -> None:
        with TemporaryDirectory() as directory:
            history = VacancyHistory(Path(directory) / "history.sqlite3")
            now = datetime(2026, 9, 12, 15, 0, tzinfo=MOSCOW)
            records = (
                VacancyResponseRecord(
                    vacancy_id="1",
                    occurred_at=now - timedelta(hours=1),
                    status=VacancyResponseStatus.SENT,
                    detail="sent",
                    vacancy_title="One",
                    company_name="Example",
                    search_query="Python",
                    match_score=80,
                ),
                VacancyResponseRecord(
                    vacancy_id="2",
                    occurred_at=now - timedelta(days=1),
                    status=VacancyResponseStatus.SENT,
                    detail="sent",
                    vacancy_title="Two",
                    company_name="Example",
                    search_query="Python",
                    match_score=80,
                ),
                VacancyResponseRecord(
                    vacancy_id="3",
                    occurred_at=now,
                    status=VacancyResponseStatus.MANUAL_REQUIRED,
                    detail="questionnaire",
                    vacancy_title="Three",
                    company_name="Example",
                    search_query="Python",
                    match_score=80,
                    manual_reason=ManualResponseReason.QUESTIONNAIRE,
                ),
            )
            for record in records:
                history.reserve_unseen(
                    [f"https://hh.ru/vacancy/{record.vacancy_id}"],
                    search_query="Python",
                    limit=1,
                    revisit_after_days=0,
                )
                history.record_response(record)

            self.assertEqual(history.sent_response_count_today(now=now), 1)

    def test_response_summary_uses_moscow_date_and_only_new_sends_for_target(self) -> None:
        with TemporaryDirectory() as directory:
            history = VacancyHistory(Path(directory) / "history.sqlite3")
            records = (
                ("101", datetime(2026, 9, 26, 20, 59, tzinfo=UTC), VacancyResponseStatus.SENT),
                ("102", datetime(2026, 9, 26, 21, 0, tzinfo=UTC), VacancyResponseStatus.SENT),
                ("103", datetime(2026, 9, 27, 20, 59, tzinfo=UTC), VacancyResponseStatus.SENT),
                ("104", datetime(2026, 9, 27, 21, 0, tzinfo=UTC), VacancyResponseStatus.SENT),
                (
                    "105",
                    datetime(2026, 9, 27, 10, 0, tzinfo=UTC),
                    VacancyResponseStatus.ALREADY_SENT,
                ),
                ("106", datetime(2026, 9, 27, 11, 0, tzinfo=UTC), VacancyResponseStatus.UNKNOWN),
            )
            for vacancy_id, occurred_at, status in records:
                history.record_response(
                    VacancyResponseRecord(
                        vacancy_id=vacancy_id,
                        occurred_at=occurred_at,
                        status=status,
                        detail="test",
                        vacancy_title="Test",
                        company_name="Example",
                        search_query="Python",
                        match_score=80,
                    )
                )
            summary = history.response_summary_on(date(2026, 9, 27))
            self.assertEqual(summary.sent, 2)
            self.assertEqual(summary.already_sent, 1)
            self.assertEqual(summary.unknown, 1)
            self.assertTrue(summary.meets_target(2))
            self.assertFalse(summary.meets_target(3))
