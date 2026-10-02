from __future__ import annotations

import configparser
import json
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import datetime
from pathlib import Path

from hh_raiser.bot.models import InstanceStatistics
from hh_raiser.models import MOSCOW


class StatisticsReadError(RuntimeError):
    """Raised when existing HHRaiser state cannot be read consistently."""


def read_instance_statistics(
    state_dir: Path,
    *,
    config_file: Path | None = None,
    now: datetime | None = None,
) -> InstanceStatistics:
    """Read aggregate counters without creating or modifying the SQLite database."""

    database_path = state_dir / "vacancy-history.sqlite3"
    daily_limit, responses_enabled, matching_mode = _read_response_settings(config_file)
    if not database_path.is_file():
        return _empty_statistics(
            next_raise_at=_read_next_raise_at(state_dir / "status.json"),
            daily_limit=daily_limit,
            responses_enabled=responses_enabled,
            matching_mode=matching_mode,
        )
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
            model_table = (
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'vacancy_model_evaluations'"
                ).fetchone()
                is not None
            )
            evaluated = (
                _scalar_int(connection, "SELECT COUNT(*) FROM vacancy_model_evaluations")
                if model_table
                else 0
            )
            semantic_rows = (
                connection.execute(
                    """SELECT verdict, COUNT(*) FROM vacancy_model_evaluations
                    GROUP BY verdict"""
                ).fetchall()
                if model_table
                else []
            )
            evaluation_columns = (
                {
                    str(row[1])
                    for row in connection.execute("PRAGMA table_info(vacancy_model_evaluations)")
                }
                if model_table
                else set()
            )
            pending_responses = (
                _scalar_int(
                    connection,
                    """SELECT COUNT(DISTINCT evaluation.vacancy_id)
                    FROM vacancy_model_evaluations AS evaluation
                    WHERE evaluation.verdict = 'fit' AND evaluation.response_state = 'pending'
                      AND NOT EXISTS (
                          SELECT 1 FROM vacancy_responses AS response
                          WHERE response.vacancy_id = evaluation.vacancy_id
                            AND response.status IN
                                ('sent', 'manual_required', 'already_sent', 'unknown')
                      )""",
                )
                if "response_state" in evaluation_columns
                else 0
            )
            response_rows = connection.execute(
                "SELECT status, occurred_at FROM vacancy_responses"
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
        responses_by_status=Counter(str(status) for status, _timestamp in response_rows),
        next_raise_at=_read_next_raise_at(state_dir / "status.json"),
        today_sent=sent_counts_by_moscow_day(response_rows)[
            (now or datetime.now(MOSCOW)).astimezone(MOSCOW).date().isoformat()
        ],
        pending_responses=pending_responses,
        daily_limit=daily_limit,
        responses_enabled=responses_enabled,
        matching_mode="polza" if matching_mode else None,
        semantic_verdicts={str(verdict): int(count) for verdict, count in semantic_rows},
    )


def response_report_path(state_dir: Path) -> Path | None:
    path = state_dir / "vacancy-responses.xlsx"
    return path if path.is_file() else None


def _empty_statistics(
    *,
    next_raise_at: datetime | None,
    daily_limit: int | None,
    responses_enabled: bool | None,
    matching_mode: str | None,
) -> InstanceStatistics:
    return InstanceStatistics(
        generation=1,
        discovered=0,
        viewed_vacancies=0,
        total_views=0,
        evaluated=0,
        responses_by_status={},
        next_raise_at=next_raise_at,
        daily_limit=daily_limit,
        responses_enabled=responses_enabled,
        matching_mode=matching_mode,
    )


def sent_counts_by_moscow_day(rows: list[tuple[object, object]]) -> Counter[str]:
    """Only confirmed new sends count toward each Moscow calendar day."""
    counts: Counter[str] = Counter()
    for status, timestamp in rows:
        if status != "sent":
            continue
        try:
            occurred_at = datetime.fromisoformat(str(timestamp))
        except ValueError:
            continue
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=MOSCOW)
        counts[occurred_at.astimezone(MOSCOW).date().isoformat()] += 1
    return counts


def _read_response_settings(path: Path | None) -> tuple[int | None, bool | None, str | None]:
    if path is None or not path.is_file():
        return None, None, None
    parser = configparser.RawConfigParser(interpolation=None)
    try:
        with path.open(encoding="utf-8") as stream:
            parser.read_file(stream)
        return (
            parser.getint("responses", "daily_limit", fallback=0),
            parser.getboolean("responses", "enabled", fallback=True),
            "polza" if parser.getboolean("matching", "enabled", fallback=True) else None,
        )
    except (OSError, configparser.Error, ValueError):
        return None, None, None


def _scalar_int(connection: sqlite3.Connection, query: str, *, default: int = 0) -> int:
    row = connection.execute(query).fetchone()
    return int(row[0]) if row and row[0] is not None else default


def _read_next_raise_at(path: Path) -> datetime | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return datetime.fromisoformat(str(payload["next_raise_at"]))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
