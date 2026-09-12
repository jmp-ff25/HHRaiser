from __future__ import annotations

from html import escape

from hh_raiser.bot.models import InstanceStatistics, ManagedInstance, ServiceSnapshot

_SERVICE_LABELS = {
    "active": "🟢 Работает",
    "inactive": "⚪ Остановлен",
    "failed": "🔴 Ошибка",
    "activating": "🟡 Запускается",
    "deactivating": "🟡 Останавливается",
}
_RESPONSE_LABELS = {
    "sent": "успешно отправлено",
    "manual_required": "нужно участие кандидата",
    "already_sent": "уже было отправлено",
    "unavailable": "недоступно",
    "unknown": "неизвестный результат",
    "error": "техническая ошибка",
}


def format_service_status(instance: ManagedInstance, snapshot: ServiceSnapshot) -> str:
    """Describe a service in human terms without exposing server internals."""

    state = _SERVICE_LABELS.get(snapshot.active_state, f"⚪ {snapshot.active_state}")
    process = f"\nПроцесс: <code>{snapshot.main_pid}</code>" if snapshot.main_pid else ""
    return (
        f"<b>{escape(instance.name)}</b>\n"
        f"Состояние: {escape(state)}\n"
        f"Служба: <code>{escape(instance.service_name)}</code>{process}"
    )


def format_statistics(instance: ManagedInstance, statistics: InstanceStatistics) -> str:
    """Render compact counters understandable without knowledge of the database schema."""

    average = (
        f"{statistics.average_match_score:.1f}%"
        if statistics.average_match_score is not None
        else "ещё нет данных"
    )
    next_raise = (
        statistics.next_raise_at.strftime("%d.%m.%Y %H:%M %Z")
        if statistics.next_raise_at
        else "пока неизвестно"
    )
    response_lines = [
        f"• {_RESPONSE_LABELS.get(status, status)}: {count}"
        for status, count in sorted(statistics.responses_by_status.items())
    ]
    responses = "\n".join(response_lines) if response_lines else "• откликов пока нет"
    return (
        f"<b>Статистика: {escape(instance.name)}</b>\n\n"
        f"Найдено уникальных вакансий: <b>{statistics.discovered}</b>\n"
        f"Просмотрено уникальных вакансий: <b>{statistics.viewed_vacancies}</b>\n"
        f"Всего содержательных просмотров: <b>{statistics.total_views}</b>\n"
        f"Оценено вакансий: <b>{statistics.evaluated}</b>\n"
        f"Средняя оценка соответствия: <b>{average}</b>\n"
        f"Текущий цикл уникальных просмотров: <b>№ {statistics.generation}</b>\n"
        f"Следующее поднятие резюме: <b>{escape(next_raise)}</b>\n\n"
        f"<b>Отклики</b>\n{responses}"
    )


def format_logs(logs: str, *, maximum_length: int = 3500) -> str:
    """Fit escaped journal output into one Telegram message."""

    tail = logs[-maximum_length:]
    prefix = "…\n" if len(logs) > maximum_length else ""
    return f"<b>Последние события</b>\n<pre>{escape(prefix + tail)}</pre>"


def format_periodic_summary(
    instance: ManagedInstance,
    snapshot: ServiceSnapshot,
    statistics: InstanceStatistics,
) -> str:
    """Render a short scheduled report that remains readable with several instances."""

    state = _SERVICE_LABELS.get(snapshot.active_state, snapshot.active_state)
    successful = statistics.responses_by_status.get("sent", 0)
    manual = statistics.responses_by_status.get("manual_required", 0)
    return (
        f"<b>{escape(instance.name)}</b> — {escape(state)}\n"
        f"Вакансий найдено: {statistics.discovered}; просмотров: {statistics.total_views}; "
        f"откликов отправлено: {successful}; требуют участия: {manual}."
    )
