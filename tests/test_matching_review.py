from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from hh_raiser.reporting.matching_review import read_matching_disagreements


class MatchingReviewTests(unittest.TestCase):
    def test_only_shadow_disagreements_and_latest_result_are_reported(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "activity-events.jsonl"
            entries = [
                {
                    "metadata": {
                        "semantic_mode": "lexical",
                        "vacancy_url": "https://hh.ru/vacancy/1",
                    }
                },
                {
                    "metadata": {
                        "semantic_mode": "shadow",
                        "vacancy_url": "https://hh.ru/vacancy/1",
                        "vacancy_title": "RAG developer",
                        "match_score": 40,
                        "match_accepted": False,
                        "semantic_verdict": "fit",
                        "semantic_reason": "Python backend",
                    }
                },
                {
                    "metadata": {
                        "semantic_mode": "shadow",
                        "vacancy_url": "https://hh.ru/vacancy/2",
                        "vacancy_title": "Java developer",
                        "match_score": 75,
                        "match_accepted": True,
                        "semantic_verdict": "unfit",
                        "semantic_reason": "Java primary",
                    }
                },
                {
                    "metadata": {
                        "semantic_mode": "shadow",
                        "vacancy_url": "https://hh.ru/vacancy/1",
                        "vacancy_title": "RAG developer",
                        "match_score": 43,
                        "match_accepted": False,
                        "semantic_verdict": "fit",
                        "semantic_reason": "основная задача: «Разрабатывать API на Python»",
                    }
                },
            ]
            path.write_text("\n".join(json.dumps(item) for item in entries), encoding="utf-8")
            disagreements = read_matching_disagreements(path)
            self.assertEqual(
                [item.url for item in disagreements],
                ["https://hh.ru/vacancy/1", "https://hh.ru/vacancy/2"],
            )
            self.assertEqual(
                disagreements[0].reason,
                "основная задача: «Разрабатывать API на Python»",
            )

    def test_model_claims_are_not_repeated_as_facts(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "activity-events.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "metadata": {
                            "semantic_mode": "shadow",
                            "vacancy_url": "https://hh.ru/vacancy/4",
                            "match_score": 20,
                            "match_accepted": False,
                            "semantic_verdict": "fit",
                            "semantic_reason": "Кандидат работал с React в продакшене.",
                            "semantic_gaps": "LangGraph не упомянут в резюме, опыт QA доказан",
                        }
                    }
                ),
                encoding="utf-8",
            )
            result = read_matching_disagreements(path)[0]
            self.assertNotIn("React в продакшене", result.reason)
            self.assertEqual(result.gaps, "LangGraph не упомянут в резюме")

    def test_later_agreement_removes_old_disagreement(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "activity-events.jsonl"
            base = {
                "semantic_mode": "shadow",
                "vacancy_url": "https://hh.ru/vacancy/3",
                "match_score": 40,
                "match_accepted": False,
            }
            path.write_text(
                "\n".join(
                    json.dumps({"metadata": {**base, "semantic_verdict": verdict}})
                    for verdict in ("fit", "unfit")
                ),
                encoding="utf-8",
            )
            self.assertEqual(read_matching_disagreements(path), [])


if __name__ == "__main__":
    unittest.main()
