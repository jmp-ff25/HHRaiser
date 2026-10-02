"""Data contracts for model-based vacancy matching."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class VacancyDocument:
    title: str
    description: str
    skills: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelDecision:
    verdict: str
    reason: str
    gaps: tuple[str, ...]
    input_tokens: int = 0
    output_tokens: int = 0
    cost_rub: float | None = None


class InvalidModelResponse(ValueError):
    """The paid API request succeeded but did not produce a usable decision."""


class VacancyDecisionSource(Protocol):
    def evaluate(
        self, *, resume_title: str, resume_text: str, vacancy: VacancyDocument, prompt: str
    ) -> ModelDecision: ...


class ModelEvaluationStore(Protocol):
    def model_evaluation(self, url: str, resume_fingerprint: str) -> ModelDecision | None: ...

    def record_model_evaluation(
        self, url: str, *, resume_fingerprint: str, model: str, decision: ModelDecision
    ) -> bool: ...


@dataclass(frozen=True)
class MatchAssessment:
    accepted: bool
    applied: bool
    semantic_verdict: str
    semantic_reason: str
    semantic_gaps: tuple[str, ...] = ()
    cached: bool = False
    score: None = None
    semantic_mode: str = "polza"
