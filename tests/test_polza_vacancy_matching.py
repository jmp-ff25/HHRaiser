from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock

from openai import APIConnectionError

from hh_raiser.application.polza_vacancy_matcher import (
    PolzaVacancyMatcher,
    concise_log_reason,
    resume_fingerprint,
)
from hh_raiser.domain.matching import InvalidModelResponse, ModelDecision, VacancyDocument
from hh_raiser.infrastructure.polza_vacancy_client import (
    PolzaVacancyClient,
)
from hh_raiser.infrastructure.storage.vacancy_history import VacancyHistory


class PolzaVacancyClientTests(unittest.TestCase):
    def test_sends_strict_json_and_disables_reasoning(self) -> None:
        transport = Mock()
        transport.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content=json.dumps({
                    "verdict": "fit", "reason": "Совпадают задачи", "gaps": []
                })),
            )],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20),
        )
        client = PolzaVacancyClient(api_key="test", client=transport)
        decision = client.evaluate(
            resume_title="Python backend", resume_text="FastAPI",
            vacancy=VacancyDocument("AI developer", "Python и RAG"),
            prompt="Сравни реальные обязанности.",
        )
        request = transport.chat.completions.create.call_args.kwargs
        self.assertEqual(request["model"], "deepseek/deepseek-v4.1-flash")
        self.assertEqual(request["extra_body"], {"reasoning": {"enabled": False}})
        self.assertEqual(request["response_format"]["type"], "json_schema")
        self.assertEqual(decision.verdict, "fit")
        self.assertEqual((decision.input_tokens, decision.output_tokens), (100, 20))

    def test_empty_or_unfinished_response_is_rejected(self) -> None:
        for choices in ([], [SimpleNamespace(
            finish_reason="length",
            message=SimpleNamespace(content='{"verdict":"fit","reason":"yes","gaps":[]}'),
        )]):
            with self.subTest(choices=choices):
                transport = Mock()
                transport.chat.completions.create.return_value = SimpleNamespace(
                    choices=choices, usage=None
                )
                client = PolzaVacancyClient(api_key="test", client=transport)
                with self.assertRaises(InvalidModelResponse):
                    client.evaluate(
                        resume_title="Python", resume_text="Backend",
                        vacancy=VacancyDocument("Python", "Backend"), prompt="Оцени",
                    )


class PolzaVacancyMatcherTests(unittest.TestCase):
    def test_log_reason_has_one_ending_and_never_cuts_a_word(self) -> None:
        self.assertEqual(concise_log_reason("Совпадают задачи."), "Совпадают задачи.")
        self.assertEqual(concise_log_reason("Совпадают задачи"), "Совпадают задачи.")
        self.assertEqual(concise_log_reason("Очень длинное объяснение", limit=13), "Очень длинное…")

    def test_paid_answer_is_persisted_before_second_visit(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "vacancy-history.sqlite3"
            source = Mock()
            source.evaluate.return_value = ModelDecision("fit", "Python backend", ())
            vacancy = VacancyDocument("AI developer", "Python backend и RAG")
            url = "https://hh.ru/vacancy/123"
            first = PolzaVacancyMatcher(
                history=VacancyHistory(path), source=source, resume_title="Python",
                resume_text="FastAPI PostgreSQL", prompt="Оцени", model="deepseek",
            ).evaluate(vacancy, url)
            second = PolzaVacancyMatcher(
                history=VacancyHistory(path), source=source, resume_title="Python",
                resume_text="FastAPI   PostgreSQL", prompt="Оцени", model="deepseek",
            ).evaluate(vacancy, url)
            self.assertTrue(first.accepted)
            self.assertTrue(second.cached)
            self.assertFalse(second.accepted)
            source.evaluate.assert_called_once()

            changed = PolzaVacancyMatcher(
                history=VacancyHistory(path), source=source, resume_title="Python",
                resume_text="FastAPI и LangChain", prompt="Оцени", model="deepseek",
            ).evaluate(vacancy, url)
            self.assertFalse(changed.cached)
            self.assertEqual(source.evaluate.call_count, 2)

    def test_incomplete_paid_answer_is_cached_as_unavailable(self) -> None:
        with TemporaryDirectory() as directory:
            history = VacancyHistory(Path(directory) / "vacancy-history.sqlite3")
            source = Mock()
            source.evaluate.side_effect = InvalidModelResponse("broken")
            matcher = PolzaVacancyMatcher(
                history=history, source=source, resume_title="Python",
                resume_text="Backend", prompt="Оцени", model="deepseek",
            )
            vacancy = VacancyDocument("Python", "Backend")
            assessment = matcher.evaluate(vacancy, "https://hh.ru/vacancy/123")
            self.assertFalse(assessment.accepted)
            self.assertTrue(matcher.already_evaluated("https://hh.ru/vacancy/123"))
            self.assertEqual(
                history.model_evaluation(
                    "https://hh.ru/vacancy/123", resume_fingerprint("Python", "Backend")
                ).verdict,
                "unavailable",
            )

    def test_transport_error_does_not_cache_or_allow_response(self) -> None:
        with TemporaryDirectory() as directory:
            history = VacancyHistory(Path(directory) / "vacancy-history.sqlite3")
            source = Mock()
            source.evaluate.side_effect = APIConnectionError(request=Mock())
            matcher = PolzaVacancyMatcher(
                history=history, source=source, resume_title="Python",
                resume_text="Backend", prompt="Оцени", model="deepseek",
            )
            assessment = matcher.evaluate(
                VacancyDocument("Python", "Backend"), "https://hh.ru/vacancy/123"
            )
            self.assertFalse(assessment.accepted)
            self.assertFalse(matcher.already_evaluated("https://hh.ru/vacancy/123"))


if __name__ == "__main__":
    unittest.main()
