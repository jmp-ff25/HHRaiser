from __future__ import annotations

import random
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from hh_raiser.domain.vacancy_response import (
    ManualResponseReason,
    VacancyResponseRecord,
    VacancyResponseStatus,
)
from hh_raiser.models import MOSCOW

VACANCY_ID_PATTERN = re.compile(r"/vacancy/(\d+)$")


def vacancy_id_from_url(url: str) -> str | None:
    """Return the public HH vacancy identifier without retaining URL parameters."""
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc != "hh.ru":
        return None
    match = VACANCY_ID_PATTERN.fullmatch(parts.path)
    return match.group(1) if match else None


class VacancyHistory:
    """SQLite history of discovered, evaluated, and successfully viewed vacancies."""

    def __init__(self, path: Path, *, randomizer: random.Random | None = None) -> None:
        self.path = path
        self._random = randomizer or random.Random()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                INSERT OR IGNORE INTO metadata(key, value)
                VALUES ('current_generation', '1');

                CREATE TABLE IF NOT EXISTS vacancies (
                    vacancy_id TEXT PRIMARY KEY,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    last_viewed_at TEXT,
                    view_count INTEGER NOT NULL DEFAULT 0,
                    last_viewed_generation INTEGER,
                    last_evaluated_at TEXT,
                    last_evaluated_generation INTEGER,
                    last_match_score INTEGER,
                    last_match_accepted INTEGER,
                    reserved_generation INTEGER,
                    reserved_until TEXT
                );

                CREATE TABLE IF NOT EXISTS vacancy_queries (
                    vacancy_id TEXT NOT NULL REFERENCES vacancies(vacancy_id),
                    search_query TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    PRIMARY KEY (vacancy_id, search_query)
                );

                CREATE INDEX IF NOT EXISTS idx_vacancies_generation
                ON vacancies(last_viewed_generation);
                CREATE INDEX IF NOT EXISTS idx_vacancies_reserved_until
                ON vacancies(reserved_until);

                CREATE TABLE IF NOT EXISTS vacancy_responses (
                    vacancy_id TEXT PRIMARY KEY REFERENCES vacancies(vacancy_id),
                    occurred_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    vacancy_title TEXT NOT NULL,
                    company_name TEXT NOT NULL,
                    search_query TEXT NOT NULL,
                    match_score INTEGER,
                    manual_reason TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_vacancy_responses_status
                ON vacancy_responses(status);
                CREATE INDEX IF NOT EXISTS idx_vacancy_responses_status_time
                ON vacancy_responses(status, occurred_at);
                """
            )
            columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(vacancies)").fetchall()
            }
            migrations = {
                "last_evaluated_at": "TEXT",
                "last_evaluated_generation": "INTEGER",
                "last_match_score": "INTEGER",
                "last_match_accepted": "INTEGER",
            }
            for column, definition in migrations.items():
                if column not in columns:
                    connection.execute(f"ALTER TABLE vacancies ADD COLUMN {column} {definition}")

    @property
    def generation(self) -> int:
        with closing(self._connect()) as connection, connection:
            return self._read_generation(connection)

    def advance_generation(self) -> int:
        """Start a new logical pass without deleting historical analytics."""
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            generation = self._read_generation(connection) + 1
            connection.execute(
                "UPDATE metadata SET value = ? WHERE key = 'current_generation'",
                (str(generation),),
            )
            connection.execute(
                "UPDATE vacancies SET reserved_generation = NULL, reserved_until = NULL"
            )
        return generation

    def viewed_count(self) -> int:
        generation = self.generation
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM vacancies WHERE last_viewed_generation = ?",
                (generation,),
            ).fetchone()
        return int(row[0]) if row else 0

    def reserve_unseen(
        self,
        urls: list[str],
        *,
        search_query: str,
        limit: int,
        revisit_after_days: int,
        lease_seconds: int = 900,
    ) -> list[str]:
        """Atomically reserve eligible vacancies so future workers cannot duplicate them."""
        if limit <= 0:
            return []
        id_to_url = {
            vacancy_id: url for url in urls if (vacancy_id := vacancy_id_from_url(url)) is not None
        }
        if not id_to_url:
            return []

        now = datetime.now(MOSCOW)
        now_text = now.isoformat()
        cutoff = (now - timedelta(days=revisit_after_days)).isoformat()
        reserved_until = (now + timedelta(seconds=lease_seconds)).isoformat()
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            generation = self._read_generation(connection)
            self._record_discovered(connection, id_to_url, search_query, now_text)
            placeholders = ",".join("?" for _ in id_to_url)
            rows = connection.execute(
                f"""
                SELECT vacancy_id
                FROM vacancies
                WHERE vacancy_id IN ({placeholders})
                  AND (last_viewed_generation IS NULL OR last_viewed_generation <> ?)
                  AND (last_evaluated_generation IS NULL OR last_evaluated_generation <> ?)
                  AND (last_viewed_at IS NULL OR last_viewed_at <= ?)
                  AND (reserved_until IS NULL OR reserved_until <= ?)
                """,
                (*id_to_url, generation, generation, cutoff, now_text),
            ).fetchall()
            candidate_ids = [str(row[0]) for row in rows]
            self._random.shuffle(candidate_ids)
            selected_ids = candidate_ids[:limit]
            if selected_ids:
                selected_placeholders = ",".join("?" for _ in selected_ids)
                connection.execute(
                    f"""
                    UPDATE vacancies
                    SET reserved_generation = ?, reserved_until = ?
                    WHERE vacancy_id IN ({selected_placeholders})
                    """,
                    (generation, reserved_until, *selected_ids),
                )
        return [id_to_url[vacancy_id] for vacancy_id in selected_ids]

    def mark_viewed(self, url: str) -> None:
        vacancy_id = vacancy_id_from_url(url)
        if vacancy_id is None:
            return
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            generation = self._read_generation(connection)
            connection.execute(
                """
                UPDATE vacancies
                SET last_viewed_at = ?, view_count = view_count + 1,
                    last_viewed_generation = ?, reserved_generation = NULL,
                    reserved_until = NULL
                WHERE vacancy_id = ?
                """,
                (datetime.now(MOSCOW).isoformat(), generation, vacancy_id),
            )

    def mark_evaluated(self, url: str, *, score: int, accepted: bool) -> None:
        vacancy_id = vacancy_id_from_url(url)
        if vacancy_id is None:
            return
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            generation = self._read_generation(connection)
            connection.execute(
                """
                UPDATE vacancies
                SET last_evaluated_at = ?, last_evaluated_generation = ?,
                    last_match_score = ?, last_match_accepted = ?,
                    reserved_generation = NULL, reserved_until = NULL
                WHERE vacancy_id = ?
                """,
                (
                    datetime.now(MOSCOW).isoformat(),
                    generation,
                    score,
                    int(accepted),
                    vacancy_id,
                ),
            )

    def has_response_record(self, url: str) -> bool:
        vacancy_id = vacancy_id_from_url(url)
        if vacancy_id is None:
            return False
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT 1 FROM vacancy_responses WHERE vacancy_id = ?",
                (vacancy_id,),
            ).fetchone()
        return row is not None

    def record_response(self, record: VacancyResponseRecord) -> bool:
        """Persist the first terminal response outcome and reject automatic retries."""
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO vacancy_responses(
                    vacancy_id, occurred_at, status, detail, vacancy_title,
                    company_name, search_query, match_score, manual_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.vacancy_id,
                    record.occurred_at.isoformat(),
                    record.status.value,
                    record.detail,
                    record.vacancy_title,
                    record.company_name,
                    record.search_query,
                    record.match_score,
                    record.manual_reason.value if record.manual_reason else None,
                ),
            )
        return cursor.rowcount > 0

    def response_records(self) -> list[VacancyResponseRecord]:
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                """
                SELECT vacancy_id, occurred_at, status, detail, vacancy_title,
                       company_name, search_query, match_score, manual_reason
                FROM vacancy_responses
                ORDER BY occurred_at DESC
                """
            ).fetchall()
        return [
            VacancyResponseRecord(
                vacancy_id=str(row[0]),
                occurred_at=datetime.fromisoformat(str(row[1])),
                status=VacancyResponseStatus(str(row[2])),
                detail=str(row[3]),
                vacancy_title=str(row[4]),
                company_name=str(row[5]),
                search_query=str(row[6]),
                match_score=int(row[7]) if row[7] is not None else None,
                manual_reason=(ManualResponseReason(str(row[8])) if row[8] else None),
            )
            for row in rows
        ]

    def sent_response_count_today(self, *, now: datetime | None = None) -> int:
        """Count successful responses for the current Moscow calendar day."""

        current = (now or datetime.now(MOSCOW)).astimezone(MOSCOW)
        day_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
        next_day = day_start + timedelta(days=1)
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                """
                SELECT COUNT(*) FROM vacancy_responses
                WHERE status = ? AND occurred_at >= ? AND occurred_at < ?
                """,
                (
                    VacancyResponseStatus.SENT.value,
                    day_start.isoformat(),
                    next_day.isoformat(),
                ),
            ).fetchone()
        return int(row[0]) if row else 0

    def release(self, url: str) -> None:
        vacancy_id = vacancy_id_from_url(url)
        if vacancy_id is None:
            return
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                UPDATE vacancies
                SET reserved_generation = NULL, reserved_until = NULL
                WHERE vacancy_id = ?
                """,
                (vacancy_id,),
            )

    @staticmethod
    def _read_generation(connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT value FROM metadata WHERE key = 'current_generation'"
        ).fetchone()
        return int(row[0]) if row else 1

    @staticmethod
    def _record_discovered(
        connection: sqlite3.Connection,
        id_to_url: dict[str, str],
        search_query: str,
        observed_at: str,
    ) -> None:
        for vacancy_id in id_to_url:
            connection.execute(
                """
                INSERT INTO vacancies(vacancy_id, first_seen_at, last_seen_at)
                VALUES (?, ?, ?)
                ON CONFLICT(vacancy_id) DO UPDATE SET last_seen_at = excluded.last_seen_at
                """,
                (vacancy_id, observed_at, observed_at),
            )
            connection.execute(
                """
                INSERT INTO vacancy_queries(
                    vacancy_id, search_query, first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(vacancy_id, search_query)
                DO UPDATE SET last_seen_at = excluded.last_seen_at
                """,
                (vacancy_id, search_query, observed_at, observed_at),
            )
