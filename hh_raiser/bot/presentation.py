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
    "sent": "отправлено HHRaiser и подтверждено HH",
    "manual_required": "не отправлено: требуется ваше участие",
    "already_sent": "отклик уже существовал до обработки HHRaiser",
    "unavailable": "недоступно",
    "unknown": "неизвестный результат",
    "error": "техническая ошибка",
}
_SEMANTIC_LABELS = {
    "fit": "подходит",
    "unsure": "нужна ручная проверка",
    "unfit": "не подходит",
    "unavailable": "оценка недоступна",
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

    next_raise = (
        statistics.next_raise_at.strftime("%d.%m.%Y %H:%M %Z")
        if statistics.next_raise_at
        else "пока неизвестно"
    )
    response_lines = [
        f"• {_RESPONSE_LABELS.get(status, status)}: {count}"
        for status, count in sorted(statistics.responses_by_status.items())
    ]
    responses = (
        f"Учтено исходов обработки вакансий: <b>{sum(statistics.responses_by_status.values())}</b>\n"
        + "\n".join(response_lines)
        if response_lines
        else "• откликов пока нет"
    )
    semantic_lines = [
        f"• {_SEMANTIC_LABELS.get(verdict, verdict)}: {statistics.semantic_verdicts[verdict]}"
        for verdict in ("fit", "unsure", "unfit", "unavailable")
        if statistics.semantic_verdicts.get(verdict, 0)
    ]
    semantic = (
        "\nОценки Polza AI по вакансиям:\n" + "\n".join(semantic_lines)
        if semantic_lines
        else "\nОценок Polza AI пока нет."
        if statistics.matching_mode == "polza"
        else ""
    )
    return (
        f"<b>Статистика: {escape(instance.name)}</b>\n\n"
        f"Найдено уникальных вакансий: <b>{statistics.discovered}</b>\n"
        f"Просмотрено уникальных вакансий: <b>{statistics.viewed_vacancies}</b>\n"
        f"Всего содержательных просмотров: <b>{statistics.total_views}</b>\n"
        f"Оценок через Polza AI: <b>{statistics.evaluated}</b>{semantic}\n"
        f"Подходящих вакансий ожидают отклика: <b>{statistics.pending_responses}</b>\n"
        f"Текущий цикл уникальных просмотров: <b>№ {statistics.generation}</b>\n"
        f"Следующее поднятие резюме: <b>{escape(next_raise)}</b>\n\n"
        f"<b>Подтверждённые отклики сегодня (Москва): "
        f"{_today_progress(statistics)}</b>\n"
        f"<b>Исходы за всё время</b>\n{responses}"
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
    already_sent = statistics.responses_by_status.get("already_sent", 0)
    manual = statistics.responses_by_status.get("manual_required", 0)
    return (
        f"<b>{escape(instance.name)}</b> — {escape(state)}\n"
        f"Вакансий найдено: {statistics.discovered}; просмотров: {statistics.total_views}; "
        f"подтверждено сегодня: {_today_progress(statistics)}; "
        f"ожидают отклика: {statistics.pending_responses}; "
        f"отправлено HHRaiser за всё время: {successful}; уже были отправлены: {already_sent}; "
        f"требуют вашего участия: {manual}."
    )


def _today_progress(statistics: InstanceStatistics) -> str:
    if statistics.responses_enabled is False:
        return f"{statistics.today_sent} (автоотклики выключены)"
    if statistics.daily_limit is not None and statistics.daily_limit > 0:
        return f"{statistics.today_sent} из {statistics.daily_limit}"
    if statistics.daily_limit == 0:
        return f"{statistics.today_sent} (без дневного лимита)"
    return str(statistics.today_sent)
