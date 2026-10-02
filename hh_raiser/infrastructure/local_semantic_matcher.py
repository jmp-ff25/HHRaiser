"""Optional local semantic assessment of a vacancy against one resume."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from hh_raiser.config import DEFAULT_MATCHING_PROMPT
from hh_raiser.domain.matching import (
    MatchAssessment,
    VacancyCompatibilityMatcher,
    VacancyDocument,
)
from hh_raiser.logging_config import LOGGER, LogEvent, event_data

LOCAL_OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
LOCAL_OLLAMA_TIMEOUT_SECONDS = 240


_EXCLUDED_ROLE_PATTERNS = (
    (re.compile(r"\bfull[ -]?stack\b|фулл?ст[еэ]к", re.IGNORECASE), "fullstack"),
    (re.compile(r"\bdwh\b|разработчик\s+хранилищ[а-я\s]*данных", re.IGNORECASE), "DWH"),
    (re.compile(r"\betl\b|\bdata engineer\b", re.IGNORECASE), "ETL/Data Engineering"),
    (
        re.compile(
            r"(?:стаж[её]р|intern).{0,24}\bml\b|\bml\b.{0,24}(?:стаж[её]р|intern)", re.IGNORECASE
        ),
        "стажировка ML",
    ),
    (
        re.compile(r"исследовател[ья]\s+машинного\s+обучения|\bml\s+researcher\b", re.IGNORECASE),
        "исследования ML",
    ),
    (
        re.compile(r"компьютерн\w*\s+зрен|\bcomputer vision\b|\bopencv\b", re.IGNORECASE),
        "компьютерное зрение",
    ),
    (
        re.compile(r"встраиваем|\bembedded\b|\bhardware\b|робототех|\brobotics\b", re.IGNORECASE),
        "встраиваемое ПО/аппаратная разработка",
    ),
    (
        re.compile(r"\b(?:a?qa|sdet|quality assurance)\b|тестировщик|автотест", re.IGNORECASE),
        "QA/автотестирование",
    ),
    (
        re.compile(
            r"\bjava\s*[- /]?\s*(?:backend|developer|разработчик)\b|"
            r"\b(?:backend[- ]разработчик|разработчик[- ]backend)\s+java\b|"
            r"\b(?:backend|разработчик)\s+java\b",
            re.IGNORECASE,
        ),
        "Java backend",
    ),
    (re.compile(r"преподавател|учител|репетитор", re.IGNORECASE), "преподавание"),
    (
        re.compile(r"(?:ментор|наставник).*(?:урок|обучен|диагностик)", re.IGNORECASE),
        "менторство на уроках",
    ),
)


def excluded_role(title: str, excluded_titles: tuple[str, ...] | None = None) -> str | None:
    """Honor explicit career preferences before asking a probabilistic model."""
    if excluded_titles is not None:
        return next((term for term in excluded_titles if term.casefold() in title.casefold()), None)
    for pattern, reason in _EXCLUDED_ROLE_PATTERNS:
        if pattern.search(title):
            return reason
    return None


_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["fit", "unsure", "unfit"]},
        "reason": {"type": "string"},
        "gaps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["verdict", "reason", "gaps"],
    "additionalProperties": False,
}

VERDICT_LABELS = {
    "fit": "подходит",
    "unsure": "нужна ручная проверка",
    "unfit": "не подходит",
}

_NAMED_TECHNOLOGIES = (
    "LangGraph",
    "LangChain",
    "RAG",
    "MCP",
    "Qdrant",
    "Ollama",
    "vLLM",
)


def unmentioned_technologies(resume_text: str, vacancy: VacancyDocument) -> tuple[str, ...]:
    """State only that a technology is not mentioned, never infer lack of experience."""
    vacancy_text = " ".join((vacancy.title, vacancy.description, *vacancy.skills))
    return tuple(
        f"{technology} не упомянут в резюме"
        for technology in _NAMED_TECHNOLOGIES
        if re.search(rf"\b{re.escape(technology)}\b", vacancy_text, re.IGNORECASE)
        and not re.search(rf"\b{re.escape(technology)}\b", resume_text, re.IGNORECASE)
    )


_TASK_HEADINGS = (
    "обязанности",
    "задачи",
    "что нужно будет делать",
    "чем предстоит заниматься",
    "тебе предстоит",
    "что предстоит делать",
    "чем будете заниматься",
    "что будете делать",
    "основные задачи",
)
_OTHER_HEADINGS = {"требования", "что для нас важно", "условия", "мы предлагаем", "ожидания"}
_TASK_WORDS = re.compile(
    r"разработ|созда|проектир|поддерж|интеграц|программир|обучени|проводить|"
    r"анализ|автоматизац|реализац|писать|построени|улучшени|оптимизац|работа с",
    re.IGNORECASE,
)


def vacancy_task_excerpt(description: str) -> str | None:
    """Take a short, verifiable task excerpt directly from the vacancy text."""

    def shorten(value: str) -> str:
        if len(value) > 160:
            fragment = value[:160]
            clause_end = max(fragment.rfind("("), fragment.rfind("; "), fragment.rfind(". "))
            value = fragment[:clause_end] if clause_end >= 70 else fragment.rsplit(" ", 1)[0] + "…"
        return value.rstrip(" ,;:")

    lines = [" ".join(line.split()).strip(" •-–—") for line in description.splitlines()]
    lines = [line for line in lines if line]
    for index, line in enumerate(lines):
        if line.casefold().rstrip(":") in _TASK_HEADINGS:
            tasks = []
            for candidate in lines[index + 1 :]:
                if candidate.casefold().rstrip(":") in _OTHER_HEADINGS or (
                    candidate.endswith(":") and len(candidate) < 80
                ):
                    break
                tasks.append(candidate)
                if len(tasks) == 2:
                    break
            if tasks:
                return shorten("; ".join(tasks))
    for line in lines:
        if _TASK_WORDS.search(line) and len(line) >= 25:
            return shorten(line)
    return None


@dataclass(frozen=True)
class SemanticDecision:
    verdict: str
    reason: str
    gaps: tuple[str, ...]


def query_local_model(
    *,
    resume_title: str,
    resume_text: str,
    vacancy: VacancyDocument,
    model: str,
    system_prompt: str = DEFAULT_MATCHING_PROMPT,
) -> SemanticDecision:
    """Use the loopback Ollama API; personal resume data never goes to Polza.ai."""
    prompt = json.dumps(
        {
            "resume_title": resume_title,
            "resume": resume_text[:16_000],
            "vacancy_title": vacancy.title,
            "vacancy_description": vacancy.description[:12_000],
            "vacancy_skills": vacancy.skills,
        },
        ensure_ascii=False,
    )
    payload = json.dumps(
        {
            "model": model,
            "stream": False,
            "think": False,
            "format": _RESPONSE_SCHEMA,
            "options": {
                "temperature": 0,
                "num_ctx": 8192 if len(prompt) <= 16_000 else 16384,
                "num_predict": 320,
            },
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        LOCAL_OLLAMA_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=LOCAL_OLLAMA_TIMEOUT_SECONDS) as response:
        content = json.load(response)["message"]["content"]
    answer = json.loads(content)
    if not isinstance(answer, dict) or answer.get("verdict") not in {"fit", "unsure", "unfit"}:
        raise ValueError("Недопустимое решение локальной модели")
    if not isinstance(answer.get("reason"), str) or not isinstance(answer.get("gaps"), list):
        raise TypeError("Неполный ответ локальной модели")
    excerpt = vacancy_task_excerpt(vacancy.description)
    return SemanticDecision(
        answer["verdict"],
        f"основная задача: «{excerpt}»"
        if excerpt
        else "основная задача не выделена; проверь описание",
        unmentioned_technologies(resume_text, vacancy),
    )


class LocalSemanticMatcher:
    """Keep lexical scoring visible while optionally using a local semantic verdict."""

    def __init__(
        self,
        *,
        resume_title: str,
        resume_text: str,
        threshold: int,
        mode: str,
        model: str,
        prompt: str = DEFAULT_MATCHING_PROMPT,
        excluded_titles: tuple[str, ...] | None = None,
    ) -> None:
        if mode not in {"shadow", "semantic"}:
            raise ValueError(f"Неизвестный режим сопоставления: {mode}")
        self.threshold = threshold
        self.mode = mode
        self.model = model
        self.prompt = prompt
        self.excluded_titles = excluded_titles
        self.resume_title = resume_title
        self.resume_text = resume_text
        self.lexical = VacancyCompatibilityMatcher(
            resume_title=resume_title, resume_text=resume_text, threshold=threshold
        )

    def evaluate(self, vacancy: VacancyDocument) -> MatchAssessment:
        lexical = self.lexical.evaluate(vacancy)
        if not lexical.applied:
            return lexical
        role = excluded_role(vacancy.title, self.excluded_titles)
        LOGGER.info(
            "Оцениваю вакансию «%s» через Ollama (ожидание до %s сек.).",
            vacancy.title,
            LOCAL_OLLAMA_TIMEOUT_SECONDS,
            extra=event_data(LogEvent.VACANCY_MATCH, vacancy_title=vacancy.title),
        )
        try:
            decision = query_local_model(
                resume_title=self.resume_title,
                resume_text=self.resume_text,
                vacancy=vacancy,
                model=self.model,
                system_prompt=self.prompt,
            )
        except (
            HTTPError,
            URLError,
            TimeoutError,
            OSError,
            ValueError,
            KeyError,
            TypeError,
        ) as error:
            if role is not None:
                LOGGER.info(
                    "Вакансия «%s»: не подходит — основная роль: %s; Ollama недоступна.",
                    vacancy.title,
                    role,
                    extra=event_data(LogEvent.VACANCY_MATCH, vacancy_title=vacancy.title),
                )
                return replace(
                    lexical,
                    accepted=lexical.accepted if self.mode == "shadow" else False,
                    semantic_mode=self.mode,
                    semantic_verdict="unfit",
                    semantic_reason=f"основная роль: {role}",
                )
            LOGGER.warning(
                "Локальная оценка вакансии недоступна (%s); режим %s.",
                error.__class__.__name__,
                self.mode,
                extra=event_data(LogEvent.VACANCY_MATCH, vacancy_title=vacancy.title),
            )
            return replace(
                lexical,
                accepted=lexical.accepted if self.mode == "shadow" else False,
                semantic_mode=self.mode,
                semantic_verdict="unavailable",
            )
        final_verdict = "unfit" if role is not None else decision.verdict
        reason = (
            f"основная роль: {role}; {decision.reason}" if role is not None else decision.reason
        )
        if role is not None:
            LOGGER.info(
                "Вакансия «%s»: не подходит — %s. Решение Ollama: %s.",
                vacancy.title,
                reason,
                VERDICT_LABELS[decision.verdict],
                extra=event_data(LogEvent.VACANCY_MATCH, vacancy_title=vacancy.title),
            )
        elif self.mode == "shadow":
            LOGGER.info(
                "Вакансия «%s»: Ollama — %s; прежний фильтр: %s%% (%s). %s",
                vacancy.title,
                VERDICT_LABELS[decision.verdict],
                lexical.score,
                "подходит" if lexical.accepted else "отклонена",
                decision.reason,
                extra=event_data(LogEvent.VACANCY_MATCH, vacancy_title=vacancy.title),
            )
        else:
            message = f"Вакансия «{vacancy.title}»: {VERDICT_LABELS[decision.verdict]} — {reason}"
            if decision.gaps:
                message += f"; {', '.join(decision.gaps)}"
            LOGGER.info(
                "%s.",
                message.rstrip("."),
                extra=event_data(LogEvent.VACANCY_MATCH, vacancy_title=vacancy.title),
            )
        return replace(
            lexical,
            accepted=lexical.accepted if self.mode == "shadow" else final_verdict == "fit",
            semantic_verdict=final_verdict,
            semantic_reason=reason,
            semantic_gaps=decision.gaps,
            semantic_mode=self.mode,
        )
