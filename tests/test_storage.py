from __future__ import annotations

import random
import sqlite3
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

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
                    """
                )

            VacancyHistory(path)

            with closing(sqlite3.connect(path)) as connection, connection:
                columns = {
                    str(row[1]) for row in connection.execute("PRAGMA table_info(vacancies)")
                }
                row = connection.execute(
                    "SELECT vacancy_id FROM vacancies WHERE vacancy_id = '123'"
                ).fetchone()
            self.assertIn("last_match_score", columns)
            self.assertIn("last_match_accepted", columns)
            self.assertEqual(row, ("123",))

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

    def test_evaluated_vacancy_is_not_reserved_again_in_same_generation(self) -> None:
        with TemporaryDirectory() as directory:
            history = VacancyHistory(Path(directory) / "vacancy-history.sqlite3")
            url = "https://hh.ru/vacancy/456"
            history.reserve_unseen([url], search_query="Python", limit=1, revisit_after_days=0)

            history.mark_evaluated(url, score=31, accepted=False)

            self.assertEqual(
                history.reserve_unseen([url], search_query="Python", limit=1, revisit_after_days=0),
                [],
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
