from __future__ import annotations

from pathlib import Path

import xlsxwriter
from xlsxwriter.exceptions import XlsxWriterException

from hh_raiser.domain.vacancy_response import (
    ManualResponseReason,
    VacancyResponseRecord,
    VacancyResponseStatus,
)

_STATUS_LABELS = {
    VacancyResponseStatus.SENT: "Успешно отправлен",
    VacancyResponseStatus.MANUAL_REQUIRED: "Требуется участие кандидата",
    VacancyResponseStatus.ALREADY_SENT: "Уже был отправлен",
    VacancyResponseStatus.UNAVAILABLE: "Отклик недоступен",
    VacancyResponseStatus.UNKNOWN: "Неизвестный результат",
    VacancyResponseStatus.ERROR: "Техническая ошибка",
}

_REASON_LABELS = {
    ManualResponseReason.QUESTIONNAIRE: "Анкета работодателя",
    ManualResponseReason.COVER_LETTER: "Обязательное сопроводительное письмо",
    ManualResponseReason.EXTERNAL_SITE: "Внешний сайт",
    ManualResponseReason.TEST: "Тестовое задание",
    ManualResponseReason.RESUME_SELECTION: "Выбор резюме",
    ManualResponseReason.AUTHENTICATION: "Вход или подтверждение контактов",
    ManualResponseReason.OTHER_FORM: "Другие обязательные поля",
}

_HEADERS = (
    "Дата и время (МСК)",
    "Статус",
    "Причина ручного действия",
    "Вакансия",
    "Компания",
    "Поисковый запрос",
    "Соответствие, %",
    "ID вакансии",
    "Ссылка",
    "Комментарий",
)


class ResponseReportError(RuntimeError):
    pass


def export_response_workbook(path: Path, records: list[VacancyResponseRecord]) -> None:
    """Rebuild the filterable Excel view from the SQLite source of truth."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.stem}.tmp{path.suffix}")
    try:
        _write_workbook(temporary_path, records)
        temporary_path.replace(path)
    except (OSError, XlsxWriterException) as error:
        temporary_path.unlink(missing_ok=True)
        raise ResponseReportError(f"Не удалось обновить Excel-журнал: {path.name}") from error


def _write_workbook(path: Path, records: list[VacancyResponseRecord]) -> None:
    workbook = xlsxwriter.Workbook(path)
    try:
        workbook.set_properties(
            {
                "title": "Журнал откликов HHRaiser",
                "subject": "Результаты автоматических и ручных откликов на вакансии",
                "author": "HHRaiser",
            }
        )
        worksheet = workbook.add_worksheet("Отклики")
        worksheet.hide_gridlines(2)
        worksheet.freeze_panes(5, 2)

        title_format = workbook.add_format({"bold": True, "font_size": 15, "font_color": "#111827"})
        note_format = workbook.add_format(
            {"italic": True, "font_color": "#64748B", "font_size": 10}
        )
        date_format = workbook.add_format({"num_format": "dd.mm.yyyy hh:mm:ss"})
        score_format = workbook.add_format({"num_format": "0"})
        link_format = workbook.add_format({"font_color": "#2563EB", "underline": True})

        worksheet.write(1, 0, "Журнал откликов HHRaiser", title_format)
        worksheet.write(
            2,
            0,
            "Используйте фильтр в строке заголовков, чтобы показать успешные отклики "
            "или вакансии, требующие вашего участия.",
            note_format,
        )

        start_row = 4
        for row_index, record in enumerate(records, start=start_row + 1):
            occurred_at = record.occurred_at.replace(tzinfo=None)
            worksheet.write_datetime(row_index, 0, occurred_at, date_format)
            worksheet.write(row_index, 1, _STATUS_LABELS[record.status])
            worksheet.write(
                row_index,
                2,
                _REASON_LABELS.get(record.manual_reason, "") if record.manual_reason else "",
            )
            worksheet.write(row_index, 3, record.vacancy_title)
            worksheet.write(row_index, 4, record.company_name)
            worksheet.write(row_index, 5, record.search_query)
            if record.match_score is not None:
                worksheet.write_number(row_index, 6, record.match_score, score_format)
            worksheet.write_string(row_index, 7, record.vacancy_id)
            worksheet.write_url(
                row_index,
                8,
                f"https://hh.ru/vacancy/{record.vacancy_id}",
                link_format,
                "Открыть",
            )
            worksheet.write(row_index, 9, record.detail)

        last_row = start_row + max(len(records), 1)
        worksheet.add_table(
            start_row,
            0,
            last_row,
            len(_HEADERS) - 1,
            {
                "name": "VacancyResponses",
                "style": "Table Style Medium 2",
                "columns": [{"header": header} for header in _HEADERS],
            },
        )
        worksheet.set_column(0, 0, 20)
        worksheet.set_column(1, 2, 31)
        worksheet.set_column(3, 5, 32)
        worksheet.set_column(6, 6, 17)
        worksheet.set_column(7, 7, 15)
        worksheet.set_column(8, 8, 13)
        worksheet.set_column(9, 9, 58)
        worksheet.set_row(start_row, 28)

        data_first_row = start_row + 1
        if records:
            worksheet.conditional_format(
                data_first_row,
                1,
                last_row,
                1,
                {
                    "type": "text",
                    "criteria": "containing",
                    "value": "Успешно",
                    "format": workbook.add_format({"bg_color": "#DCFCE7", "font_color": "#166534"}),
                },
            )
            worksheet.conditional_format(
                data_first_row,
                1,
                last_row,
                1,
                {
                    "type": "text",
                    "criteria": "containing",
                    "value": "Требуется",
                    "format": workbook.add_format({"bg_color": "#FEF3C7", "font_color": "#92400E"}),
                },
            )
    finally:
        workbook.close()
