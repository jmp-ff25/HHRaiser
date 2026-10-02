"""Coordinate a paid model decision with durable per-resume vacancy history."""

from __future__ import annotations

import hashlib

from openai import APIError

from hh_raiser.domain.matching import (
    InvalidModelResponse,
    MatchAssessment,
    ModelDecision,
    ModelEvaluationStore,
    VacancyDecisionSource,
    VacancyDocument,
)
from hh_raiser.logging_config import LOGGER, LogEvent, event_data

VERDICT_LABELS = {
    "fit": "подходит",
    "unsure": "нужна ручная проверка",
    "unfit": "не подходит",
    "unavailable": "оценка недоступна",
}


def resume_fingerprint(title: str) -> str:
    """Use the selected resume identity, not its periodically edited text."""
    normalized = " ".join(title.casefold().split())
    return hashlib.sha256(normalized.encode()).hexdigest()


class PolzaVacancyMatcher:
    """Application service; the API source and SQLite history are replaceable dependencies."""

    def __init__(
        self,
        *,
        history: ModelEvaluationStore,
        source: VacancyDecisionSource,
        resume_title: str,
        resume_text: str,
        prompt: str,
        model: str,
        auto_respond: bool = False,
    ) -> None:
        self.history = history
        self.source = source
        self.resume_title = resume_title
        self.resume_text = resume_text
        self.prompt = prompt
        self.model = model
        self.auto_respond = auto_respond
        self.fingerprint = resume_fingerprint(resume_title)

    def already_evaluated(self, url: str) -> bool:
        return self.history.model_evaluation(url, self.fingerprint) is not None

    def response_pending(self, url: str) -> bool:
        return self.history.pending_model_response(url, self.fingerprint)

    def evaluate(self, vacancy: VacancyDocument, url: str) -> MatchAssessment:
        cached = self.history.model_evaluation(url, self.fingerprint)
        if cached is not None:
            return self._assessment(
                cached,
                cached=True,
                response_pending=self.auto_respond and self.response_pending(url),
            )
        try:
            decision = self.source.evaluate(
                resume_title=self.resume_title,
                resume_text=self.resume_text,
                vacancy=vacancy,
                prompt=self.prompt,
            )
        except InvalidModelResponse:
            # A 200 response can be billed even if its JSON was incomplete.
            decision = ModelDecision("unavailable", "Ответ модели неполон", ())
            self.history.record_model_evaluation(
                url,
                resume_fingerprint=self.fingerprint,
                model=self.model,
                decision=decision,
            )
        except (APIError, OSError, TimeoutError) as error:
            LOGGER.warning(
                "Оценка Polza для вакансии «%s» недоступна (%s); отклик запрещён.",
                vacancy.title,
                error.__class__.__name__,
                extra=event_data(LogEvent.VACANCY_MATCH, vacancy_title=vacancy.title),
            )
            return self._assessment(ModelDecision("unavailable", "Ошибка API Polza", ()))
        else:
            inserted = self.history.record_model_evaluation(
                url,
                resume_fingerprint=self.fingerprint,
                model=self.model,
                decision=decision,
                pending_response=self.auto_respond,
            )
            if not inserted:
                stored = self.history.model_evaluation(url, self.fingerprint)
                return self._assessment(
                    stored or decision,
                    cached=True,
                    response_pending=self.auto_respond and self.response_pending(url),
                )
        reason = concise_log_reason(decision.reason)
        LOGGER.info(
            "Polza «%s»: %s — %s",
            vacancy.title,
            VERDICT_LABELS[decision.verdict],
            reason,
            extra=event_data(
                LogEvent.VACANCY_MATCH,
                vacancy_title=vacancy.title,
                semantic_verdict=decision.verdict,
                model=self.model,
            ),
        )
        return self._assessment(decision, response_pending=self.auto_respond)

    @staticmethod
    def _assessment(
        decision: ModelDecision, *, cached: bool = False, response_pending: bool = False
    ) -> MatchAssessment:
        return MatchAssessment(
            accepted=decision.verdict == "fit" and (not cached or response_pending),
            applied=decision.verdict in {"fit", "unsure", "unfit"},
            semantic_verdict=decision.verdict,
            semantic_reason=decision.reason,
            semantic_gaps=decision.gaps,
            cached=cached,
        )


def concise_log_reason(reason: str, *, limit: int = 280) -> str:
    """Keep log explanations readable while retaining the full answer in SQLite."""
    normalized = " ".join(reason.split())
    if len(normalized) > limit:
        prefix = normalized[:limit]
        first_words = prefix if normalized[limit].isspace() else prefix.rsplit(" ", 1)[0] or prefix
        return first_words.rstrip(".,;:") + "…"
    return normalized if normalized.endswith((".", "!", "?", "…")) else normalized + "."
