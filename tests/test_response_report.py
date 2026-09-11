from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from hh_raiser.domain.vacancy_response import (
    ManualResponseReason,
    VacancyResponseRecord,
    VacancyResponseStatus,
)
from hh_raiser.models import MOSCOW
from hh_raiser.reporting.response_report import export_response_workbook


class ResponseReportTests(unittest.TestCase):
    def test_exports_filterable_excel_table_with_statuses_and_links(self) -> None:
        records = [
            VacancyResponseRecord(
                vacancy_id="122",
                occurred_at=datetime(2026, 9, 11, 12, 20, tzinfo=MOSCOW),
                status=VacancyResponseStatus.SENT,
                detail="HH подтвердил отправку.",
                vacancy_title="Backend developer",
                company_name="Example 2",
                search_query="Backend",
                match_score=82,
            ),
            VacancyResponseRecord(
                vacancy_id="123",
                occurred_at=datetime(2026, 9, 11, 12, 30, tzinfo=MOSCOW),
                status=VacancyResponseStatus.MANUAL_REQUIRED,
                detail="Нужно заполнить анкету.",
                vacancy_title="Python developer",
                company_name="Example",
                search_query="Python",
                match_score=78,
                manual_reason=ManualResponseReason.QUESTIONNAIRE,
            ),
        ]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "responses.xlsx"
            export_response_workbook(path, records)
            with ZipFile(path) as archive:
                table_xml = archive.read("xl/tables/table1.xml").decode("utf-8")
                strings_xml = archive.read("xl/sharedStrings.xml").decode("utf-8")
                links_xml = archive.read("xl/worksheets/_rels/sheet1.xml.rels").decode("utf-8")

        self.assertIn("autoFilter", table_xml)
        self.assertIn("Статус", table_xml)
        self.assertIn("Успешно отправлен", strings_xml)
        self.assertIn("Требуется участие кандидата", strings_xml)
        self.assertIn("https://hh.ru/vacancy/123", links_xml)
