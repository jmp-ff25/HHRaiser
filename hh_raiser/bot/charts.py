from __future__ import annotations

import sqlite3
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

from hh_raiser.models import MOSCOW

_BACKGROUND = "#0b1220"
_PANEL = "#111c2e"
_TEXT = "#f8fafc"
_MUTED = "#a8b3c7"
_GRID = "#334155"
_ACCENT = "#38bdf8"
_SUCCESS = "#34d399"
_WARNING = "#fbbf24"
_DANGER = "#fb7185"
_PURPLE = "#a78bfa"

_RESPONSE_LABELS = {
    "sent": "Отправлено",
    "manual_required": "Нужно участие",
    "already_sent": "Отправлено ранее",
    "unavailable": "Недоступно",
    "unknown": "Неизвестно",
    "error": "Ошибка",
}
_RESPONSE_COLORS = {
    "sent": _SUCCESS,
    "manual_required": _WARNING,
    "already_sent": _ACCENT,
    "unavailable": _MUTED,
    "unknown": _PURPLE,
    "error": _DANGER,
}


class ChartReadError(RuntimeError):
    """Raised when a statistics dashboard cannot be built from instance state."""


def render_statistics_dashboard(state_dir: Path, *, instance_name: str) -> bytes:
    """Построить обезличенный PNG-дашборд непосредственно из SQLite-истории экземпляра."""

    # Matplotlib и NumPy загружаются только по запросу: постоянно работающему боту
    # не нужно тратить время запуска и память, пока владелец не попросил график.
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    database_path = state_dir / "vacancy-history.sqlite3"
    if not database_path.is_file():
        raise ChartReadError("История вакансий пока не создана.")
    try:
        uri = f"{database_path.resolve().as_uri()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=5)) as connection:
            query_rows = connection.execute(
                """
                SELECT search_query, COUNT(*)
                FROM vacancy_queries
                GROUP BY search_query
                ORDER BY COUNT(*) DESC
                """
            ).fetchall()
            match_scores = [
                int(row[0])
                for row in connection.execute(
                    "SELECT last_match_score FROM vacancies WHERE last_match_score IS NOT NULL"
                ).fetchall()
            ]
            response_rows = connection.execute(
                "SELECT status, COUNT(*) FROM vacancy_responses GROUP BY status"
            ).fetchall()
            daily_rows = connection.execute(
                """
                SELECT SUBSTR(occurred_at, 1, 10), COUNT(*)
                FROM vacancy_responses
                GROUP BY SUBSTR(occurred_at, 1, 10)
                ORDER BY SUBSTR(occurred_at, 1, 10)
                """
            ).fetchall()
    except sqlite3.Error as error:
        raise ChartReadError("Не удалось прочитать историю для построения графиков.") from error

    figure = Figure(figsize=(13, 8), facecolor=_BACKGROUND, constrained_layout=True)
    FigureCanvasAgg(figure)
    axes = figure.subplots(2, 2)
    figure.suptitle(
        f"HHRaiser · {instance_name}",
        color=_TEXT,
        fontsize=18,
        fontweight="bold",
    )
    for axis in axes.flat:
        _style_axis(axis)

    _draw_queries(axes[0, 0], query_rows)
    _draw_match_scores(axes[0, 1], match_scores)
    _draw_responses(axes[1, 0], response_rows)
    _draw_daily_activity(axes[1, 1], daily_rows)

    output = BytesIO()
    figure.savefig(output, format="png", dpi=150, facecolor=figure.get_facecolor())
    return output.getvalue()


def _style_axis(axis) -> None:
    axis.set_facecolor(_PANEL)
    axis.tick_params(colors=_MUTED, labelsize=9)
    for spine in axis.spines.values():
        spine.set_color(_GRID)
    axis.grid(axis="y", color=_GRID, linewidth=0.7, alpha=0.55)
    axis.set_axisbelow(True)


def _draw_queries(axis, rows: list[tuple[object, ...]]) -> None:
    axis.set_title("Вакансии, найденные по запросам", color=_TEXT, fontweight="bold")
    if not rows:
        _draw_empty(axis, "Поисковых данных пока нет")
        return
    labels = [_shorten(str(query), 32) for query, _count in reversed(rows[:10])]
    values = [int(count) for _query, count in reversed(rows[:10])]
    bars = axis.barh(labels, values, color=_ACCENT)
    axis.bar_label(bars, color=_TEXT, padding=4, fontsize=9)
    axis.grid(axis="x", color=_GRID, linewidth=0.7, alpha=0.55)
    axis.grid(axis="y", visible=False)
    axis.set_xlabel("Уникальных ID внутри каждого запроса", color=_MUTED)


def _draw_match_scores(axis, scores: list[int]) -> None:
    axis.set_title("Распределение оценки соответствия", color=_TEXT, fontweight="bold")
    if not scores:
        _draw_empty(axis, "Оценок соответствия пока нет")
        return
    axis.hist(scores, bins=range(0, 111, 10), color=_PURPLE, edgecolor=_BACKGROUND)
    average = sum(scores) / len(scores)
    axis.axvline(average, color=_WARNING, linewidth=2, label=f"Среднее: {average:.1f}%")
    axis.set_xlim(0, 100)
    axis.set_xlabel("Расчётное соответствие, %", color=_MUTED)
    axis.set_ylabel("Вакансий", color=_MUTED)
    legend = axis.legend(facecolor=_PANEL, edgecolor=_GRID)
    for text in legend.get_texts():
        text.set_color(_TEXT)


def _draw_responses(axis, rows: list[tuple[object, ...]]) -> None:
    axis.set_title("Исходы откликов", color=_TEXT, fontweight="bold")
    if not rows:
        _draw_empty(axis, "Откликов пока нет")
        return
    statuses = [str(status) for status, _count in rows]
    values = [int(count) for _status, count in rows]
    labels = [_RESPONSE_LABELS.get(status, status) for status in statuses]
    colors = [_RESPONSE_COLORS.get(status, _MUTED) for status in statuses]
    bars = axis.bar(labels, values, color=colors)
    axis.bar_label(bars, color=_TEXT, padding=3, fontsize=9)
    axis.tick_params(axis="x", rotation=20)
    axis.set_ylabel("Вакансий", color=_MUTED)


def _draw_daily_activity(axis, rows: list[tuple[object, ...]]) -> None:
    axis.set_title("Отклики за последние 14 дней", color=_TEXT, fontweight="bold")
    today = datetime.now(MOSCOW).date()
    dates = [today - timedelta(days=offset) for offset in range(13, -1, -1)]
    counts = Counter({str(day): int(count) for day, count in rows})
    values = [counts[date.isoformat()] for date in dates]
    labels = [date.strftime("%d.%m") for date in dates]
    axis.plot(labels, values, color=_SUCCESS, marker="o", linewidth=2.2)
    axis.fill_between(labels, values, color=_SUCCESS, alpha=0.14)
    axis.tick_params(axis="x", rotation=45)
    axis.set_ylabel("Откликов", color=_MUTED)
    axis.set_ylim(bottom=0)


def _draw_empty(axis, message: str) -> None:
    axis.text(
        0.5,
        0.5,
        message,
        color=_MUTED,
        horizontalalignment="center",
        verticalalignment="center",
        transform=axis.transAxes,
    )
    axis.set_xticks([])
    axis.set_yticks([])


def _shorten(value: str, maximum: int) -> str:
    return value if len(value) <= maximum else f"{value[: maximum - 1]}…"
