from __future__ import annotations

import unittest

from hh_raiser.domain.matching import (
    VacancyCompatibilityMatcher,
    VacancyDocument,
    tokenize,
)


class VacancyCompatibilityMatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.matcher = VacancyCompatibilityMatcher(
            resume_title="Backend Python-разработчик",
            resume_text=(
                "Backend Python-разработчик. Python, FastAPI, Django, PostgreSQL, "
                "Kafka, Redis, Docker. Разрабатывал REST API и нагруженные сервисы."
            ),
            threshold=55,
        )

    def test_accepts_vacancy_with_matching_title_skills_and_tasks(self) -> None:
        assessment = self.matcher.evaluate(
            VacancyDocument(
                title="Python backend-разработчик",
                description=(
                    "Разработка backend-сервисов и REST API на Python и FastAPI. "
                    "Работа с PostgreSQL, Kafka, Redis и Docker."
                ),
                skills=("Python", "FastAPI", "PostgreSQL", "Kafka", "Docker"),
            )
        )

        self.assertTrue(assessment.accepted)
        self.assertGreaterEqual(assessment.score, 55)

    def test_rejects_unrelated_vacancy(self) -> None:
        assessment = self.matcher.evaluate(
            VacancyDocument(
                title="Водитель-экспедитор",
                description="Доставка грузов, путевые листы и обслуживание автомобиля.",
                skills=("Водительское удостоверение",),
            )
        )

        self.assertFalse(assessment.accepted)
        self.assertLess(assessment.score, 55)

    def test_missing_resume_text_fails_open_without_claiming_a_score(self) -> None:
        matcher = VacancyCompatibilityMatcher(
            resume_title="Аналитик",
            resume_text="",
            threshold=55,
        )

        assessment = matcher.evaluate(VacancyDocument(title="Аналитик", description="SQL"))

        self.assertTrue(assessment.accepted)
        self.assertFalse(assessment.applied)

    def test_hyphen_does_not_hide_shared_title_terms(self) -> None:
        self.assertEqual(tokenize("Python-разработчик"), ["python", "разработчик"])
