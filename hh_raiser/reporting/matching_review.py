"""Summarize shadow-mode disagreements from the existing activity journal."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

_FACTUAL_GAP = re.compile(
    r"(?:LangGraph|LangChain|RAG|MCP|Qdrant|Ollama|vLLM) не упомянут в резюме",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class MatchingDisagreement:
    title: str
    url: str
    lexical_score: int
    lexical_accepted: bool
    semantic_verdict: str
    reason: str
    gaps: str


def read_matching_disagreements(path: Path, *, limit: int = 20) -> list[MatchingDisagreement]:
    if not path.is_file():
        return []
    by_url: dict[str, MatchingDisagreement] = {}
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            metadata = payload.get("metadata")
            if not isinstance(metadata, dict) or metadata.get("semantic_mode") != "shadow":
                continue
            accepted = metadata.get("match_accepted")
            verdict = metadata.get("semantic_verdict")
            score = metadata.get("match_score")
            url = metadata.get("vacancy_url")
            if (
                not isinstance(accepted, bool)
                or verdict not in {"fit", "unsure", "unfit"}
                or not isinstance(score, int)
                or not isinstance(url, str)
                or not url.startswith("https://hh.ru/vacancy/")
            ):
                continue
            by_url.pop(url, None)
            if (verdict == "fit") == accepted:
                continue
            stored_reason = metadata.get("semantic_reason")
            reason = "Причина не сохранена в старой записи; повторите просмотр вакансии."
            if (
                isinstance(stored_reason, str)
                and len(stored_reason) <= 220
                and stored_reason.startswith("основная задача: «")
                and stored_reason.endswith("»")
            ):
                reason = stored_reason
            by_url[url] = MatchingDisagreement(
                title=str(metadata.get("vacancy_title") or "Название не распознано")[:200],
                url=url,
                lexical_score=score,
                lexical_accepted=accepted,
                semantic_verdict=verdict,
                reason=reason,
                gaps=", ".join(
                    dict.fromkeys(
                        _FACTUAL_GAP.findall(str(metadata.get("semantic_gaps") or ""))
                    )
                ),
            )
    return list(reversed(list(by_url.values())))[:limit]
