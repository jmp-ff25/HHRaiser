from __future__ import annotations

import json
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import datetime
from pathlib import Path

from hh_raiser.bot.models import InstanceStatistics


class StatisticsReadError(RuntimeError):
    """Raised when existing HHRaiser state cannot be read consistently."""


def read_instance_statistics(state_dir: Path) -> InstanceStatistics:
    """Read aggregate counters without creating or modifying the SQLite database."""

    database_path = state_dir / "vacancy-history.sqlite3"
    if not database_path.is_file():
        return _empty_statistics(next_raise_at=_read_next_raise_at(state_dir / "status.json"))
    try:
        uri = f"{database_path.resolve().as_uri()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=5)) as connection:
            generation = _scalar_int(
                connection,
                "SELECT value FROM metadata WHERE key = 'current_generation'",
                default=1,
            )
            discovered = _scalar_int(connection, "SELECT COUNT(*) FROM vacancies")
            viewed = _scalar_int(
                connection,
                "SELECT COUNT(*) FROM vacancies WHERE last_viewed_at IS NOT NULL",
            )
            total_views = _scalar_int(
                connection,
                "SELECT COALESCE(SUM(view_count), 0) FROM vacancies",
            )
            evaluated = _scalar_int(
                connection,
                "SELECT COUNT(*) FROM vacancies WHERE last_evaluated_at IS NOT NULL",
            )
            average_match_score = _scalar_float(
                connection,
                "SELECT AVG(last_match_score) FROM vacancies WHERE last_match_score IS NOT NULL",
            )
            response_rows = connection.execute(
                "SELECT status, COUNT(*) FROM vacancy_responses GROUP BY status"
            ).fetchall()
    except sqlite3.Error as error:
        raise StatisticsReadError(
            f"Не удалось прочитать статистику {database_path.name}."
        ) from error
    return InstanceStatistics(
        generation=generation,
        discovered=discovered,
        viewed_vacancies=viewed,
        total_views=total_views,
        evaluated=evaluated,
        average_match_score=average_match_score,
        responses_by_status=Counter({str(status): int(count) for status, count in response_rows}),
        next_raise_at=_read_next_raise_at(state_dir / "status.json"),
    )


def response_report_path(state_dir: Path) -> Path | None:
    path = state_dir / "vacancy-responses.xlsx"
    return path if path.is_file() else None


def _empty_statistics(*, next_raise_at: datetime | None) -> InstanceStatistics:
    return InstanceStatistics(
        generation=1,
        discovered=0,
        viewed_vacancies=0,
        total_views=0,
        evaluated=0,
        average_match_score=None,
        responses_by_status={},
        next_raise_at=next_raise_at,
    )


def _scalar_int(connection: sqlite3.Connection, query: str, *, default: int = 0) -> int:
    row = connection.execute(query).fetchone()
    return int(row[0]) if row and row[0] is not None else default


def _scalar_float(connection: sqlite3.Connection, query: str) -> float | None:
    row = connection.execute(query).fetchone()
    return float(row[0]) if row and row[0] is not None else None


def _read_next_raise_at(path: Path) -> datetime | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return datetime.fromisoformat(str(payload["next_raise_at"]))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
