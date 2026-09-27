from __future__ import annotations

import json
import unittest
from unittest.mock import patch
from urllib.error import URLError

from hh_raiser.domain.matching import VacancyDocument
from hh_raiser.infrastructure.local_semantic_matcher import (
    LocalSemanticMatcher,
    SemanticDecision,
    excluded_role,
    query_local_model,
    unmentioned_technologies,
    vacancy_task_excerpt,
)


class LocalSemanticMatcherTests(unittest.TestCase):
    def test_explicit_role_preferences_do_not_reject_backend_with_mentoring(self) -> None:
        self.assertEqual(excluded_role("Fullstack-разработчик AI"), "fullstack")
        self.assertEqual(excluded_role("AQA Engineer Python"), "QA/автотестирование")
        self.assertEqual(excluded_role("Backend-разработчик Java"), "Java backend")
        self.assertEqual(excluded_role("Стажер ML-разработчик (fast track)"), "стажировка ML")
        self.assertEqual(excluded_role("Разработчик DWH"), "DWH")
        self.assertEqual(
            excluded_role("Разработчик ETL / Data Engineer"), "ETL/Data Engineering"
        )
        self.assertEqual(
            excluded_role("Инженер-разработчик системы компьютерного зрения (OpenCV)"),
            "компьютерное зрение",
        )
        self.assertEqual(
            excluded_role("Разработчик встраиваемого ПО на C++/Python (Робототехника)"),
            "встраиваемое ПО/аппаратная разработка",
        )
        self.assertEqual(excluded_role("Ментор по пробным урокам"), "менторство на уроках")
        self.assertIsNone(excluded_role("Python backend с менторством разработчиков"))
        self.assertIsNone(excluded_role("Python backend с интеграцией Java-сервиса"))
        self.assertIsNone(excluded_role("Python backend для AI-агентов"))
        self.assertIsNone(excluded_role("Fullstack-разработчик AI", ()))
        self.assertEqual(
            excluded_role("Преподаватель Python", ("Python",)),
            "Python",
        )

    def setUp(self) -> None:
        self.vacancy = VacancyDocument(
            title="Разработчик RAG-систем",
            description="Python, FastAPI, API для поиска по документам и интеграция LLM.",
            skills=("Python", "FastAPI", "RAG"),
        )

    def test_react_term_is_not_treated_as_an_automatic_skill_gap(self) -> None:
        vacancy = VacancyDocument(
            "Python AI agents",
            "Опыт паттерна ReAct и разработки на Python; React в соседней команде.",
        )
        self.assertNotIn("React не упомянут в резюме", unmentioned_technologies("Python", vacancy))

    def test_short_reason_is_taken_from_vacancy_tasks(self) -> None:
        self.assertEqual(
            vacancy_task_excerpt(
                "О компании\nСоздаём продукты для рынка.\nОбязанности:\n"
                "Разрабатывать API для поиска по документам\nИнтегрировать LLM в сервисы"
            ),
            "Разрабатывать API для поиска по документам; Интегрировать LLM в сервисы",
        )
        self.assertIsNone(vacancy_task_excerpt("ДМС, офис и корпоративные бонусы"))
        self.assertEqual(
            vacancy_task_excerpt(
                "Задачи:\nРазрабатывать Python API\nТребования:\nЗнание SQL"
            ),
            "Разрабатывать Python API",
        )

    def matcher(self, mode: str) -> LocalSemanticMatcher:
        return LocalSemanticMatcher(
            resume_title="Backend Python-разработчик",
            resume_text="Python, FastAPI, API, PostgreSQL, интеграции",
            threshold=90,
            mode=mode,
            model="qwen3:1.7b",
        )

    def test_shadow_does_not_change_lexical_rejection(self) -> None:
        with patch(
            "hh_raiser.infrastructure.local_semantic_matcher.query_local_model",
            return_value=SemanticDecision("fit", "Переносимый backend-опыт", ("RAG",)),
        ):
            assessment = self.matcher("shadow").evaluate(self.vacancy)
        self.assertFalse(assessment.accepted)
        self.assertEqual(assessment.semantic_verdict, "fit")

    def test_semantic_fit_accepts_transferable_backend_experience(self) -> None:
        with (
            patch(
                "hh_raiser.infrastructure.local_semantic_matcher.query_local_model",
                return_value=SemanticDecision("fit", "основная задача: «разработка API»", ()),
            ),
            self.assertLogs("hh_resume_raiser", level="INFO") as captured,
        ):
            assessment = self.matcher("semantic").evaluate(self.vacancy)
        self.assertTrue(assessment.accepted)
        message = "\n".join(captured.output)
        self.assertIn("подходит — основная задача", message)
        self.assertNotIn(": fit", message)
        self.assertNotIn("Пробелы: нет", message)

    def test_every_vacancy_reaches_ollama_but_other_primary_role_is_blocked(self) -> None:
        vacancy = VacancyDocument(
            "Разработчик встраиваемого ПО на C++/Python (Робототехника)",
            "Программирование роботов и микроконтроллеров.",
        )
        with patch(
            "hh_raiser.infrastructure.local_semantic_matcher.query_local_model",
            return_value=SemanticDecision("fit", "Python указан", ()),
        ) as query:
            assessment = self.matcher("semantic").evaluate(vacancy)
        query.assert_called_once()
        self.assertEqual(assessment.semantic_verdict, "unfit")
        self.assertFalse(assessment.accepted)

    def test_semantic_unsure_and_local_failure_do_not_enable_response(self) -> None:
        matcher = self.matcher("semantic")
        with patch(
            "hh_raiser.infrastructure.local_semantic_matcher.query_local_model",
            return_value=SemanticDecision("unsure", "Неясен стек", ("LLM",)),
        ):
            self.assertFalse(matcher.evaluate(self.vacancy).accepted)
        with patch(
            "hh_raiser.infrastructure.local_semantic_matcher.query_local_model",
            side_effect=URLError("offline"),
        ):
            assessment = matcher.evaluate(self.vacancy)
        self.assertFalse(assessment.accepted)
        self.assertEqual(assessment.semantic_verdict, "unavailable")

    def test_shadow_preserves_lexical_decision_when_model_is_unavailable(self) -> None:
        matcher = self.matcher("shadow")
        expected = matcher.lexical.evaluate(self.vacancy).accepted
        with patch(
            "hh_raiser.infrastructure.local_semantic_matcher.query_local_model",
            side_effect=ValueError("bad JSON"),
        ):
            assessment = matcher.evaluate(self.vacancy)
        self.assertEqual(assessment.accepted, expected)
        self.assertEqual(assessment.semantic_verdict, "unavailable")

    def test_request_stays_on_loopback_and_sends_structured_prompt(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self):
                return json.dumps(
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "verdict": "fit",
                                    "reason": "Кандидат внедрил LangGraph в продакшен.",
                                    "gaps": [],
                                }
                            )
                        }
                    }
                ).encode()

        with patch(
            "hh_raiser.infrastructure.local_semantic_matcher.urlopen",
            return_value=Response(),
        ) as send:
            result = query_local_model(
                resume_title="Python",
                resume_text="FastAPI",
                vacancy=self.vacancy,
                model="qwen3:1.7b",
                system_prompt="Пользовательская инструкция из INI",
            )
        request = send.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/chat")
        self.assertEqual(payload["messages"][0]["content"], "Пользовательская инструкция из INI")
        self.assertEqual(
            payload["format"]["properties"]["verdict"]["enum"], ["fit", "unsure", "unfit"]
        )
        self.assertEqual(result.verdict, "fit")
        self.assertIn("RAG не упомянут в резюме", result.gaps)
        self.assertNotIn("LangGraph", result.reason)
        self.assertIn("API для поиска по документам", result.reason)


if __name__ == "__main__":
    unittest.main()
