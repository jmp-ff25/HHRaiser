from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

TOKEN_PATTERN = re.compile(r"[a-zа-яё0-9][a-zа-яё0-9+#.]{1,}", re.IGNORECASE)
SPACE_PATTERN = re.compile(r"\s+")

STOP_WORDS = frozenset(
    {
        "and",
        "are",
        "for",
        "from",
        "the",
        "with",
        "без",
        "был",
        "быть",
        "вам",
        "вас",
        "все",
        "для",
        "его",
        "или",
        "как",
        "команда",
        "компании",
        "компания",
        "который",
        "мы",
        "нам",
        "наш",
        "наша",
        "нашей",
        "опыт",
        "работа",
        "работы",
        "свой",
        "также",
        "что",
        "это",
    }
)


@dataclass(frozen=True)
class VacancyDocument:
    title: str
    description: str
    skills: tuple[str, ...] = ()


@dataclass(frozen=True)
class MatchAssessment:
    score: int
    accepted: bool
    applied: bool
    title_similarity: float
    bm25f_relevance: float
    skills_coverage: float
    lexical_similarity: float


class VacancyCompatibilityMatcher:
    """Explainable CPU-only compatibility scoring for one selected resume."""

    def __init__(
        self,
        *,
        resume_title: str,
        resume_text: str,
        threshold: int,
    ) -> None:
        self.threshold = threshold
        self._resume_title_tokens = tokenize(resume_title)
        self._resume_tokens = tokenize(resume_text)
        self._resume_counts = Counter(self._resume_tokens)
        self._normalized_resume = normalize_text(resume_text)

    def evaluate(self, vacancy: VacancyDocument) -> MatchAssessment:
        if not self._resume_tokens:
            return MatchAssessment(
                score=100,
                accepted=True,
                applied=False,
                title_similarity=0.0,
                bm25f_relevance=0.0,
                skills_coverage=0.0,
                lexical_similarity=0.0,
            )

        title_tokens = tokenize(vacancy.title)
        description_tokens = tokenize(vacancy.description)
        skill_tokens = [tokenize(skill) for skill in vacancy.skills]
        title_similarity = dice_similarity(
            set(self._resume_title_tokens),
            set(title_tokens),
        )
        bm25f_relevance = bm25f_coverage(
            self._resume_counts,
            (
                (title_tokens, 3.0),
                ([token for skill in skill_tokens for token in skill], 4.0),
                (description_tokens, 1.0),
            ),
        )
        measured_skill_coverage = skills_coverage(
            vacancy.skills,
            normalized_resume=self._normalized_resume,
            resume_tokens=set(self._resume_tokens),
        )
        effective_skill_coverage = measured_skill_coverage if vacancy.skills else bm25f_relevance
        lexical_similarity = cosine_term_similarity(self._resume_tokens, description_tokens)
        logit = (
            -2.2
            + 1.8 * title_similarity
            + 2.2 * bm25f_relevance
            + 1.8 * effective_skill_coverage
            + 0.8 * lexical_similarity
        )
        score = round(100 / (1 + math.exp(-logit)))
        return MatchAssessment(
            score=score,
            accepted=score >= self.threshold,
            applied=True,
            title_similarity=title_similarity,
            bm25f_relevance=bm25f_relevance,
            skills_coverage=effective_skill_coverage,
            lexical_similarity=lexical_similarity,
        )


def normalize_text(value: str) -> str:
    return SPACE_PATTERN.sub(" ", value.lower().replace("ё", "е")).strip()


def tokenize(value: str) -> list[str]:
    return [
        token for token in TOKEN_PATTERN.findall(normalize_text(value)) if token not in STOP_WORDS
    ]


def dice_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return 2 * len(left & right) / (len(left) + len(right))


def cosine_term_similarity(left: list[str], right: list[str]) -> float:
    if not left or not right:
        return 0.0
    left_counts = Counter(left)
    right_counts = Counter(right)
    shared = left_counts.keys() & right_counts.keys()
    numerator = sum(left_counts[token] * right_counts[token] for token in shared)
    left_norm = math.sqrt(sum(count * count for count in left_counts.values()))
    right_norm = math.sqrt(sum(count * count for count in right_counts.values()))
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0


def skills_coverage(
    skills: tuple[str, ...],
    *,
    normalized_resume: str,
    resume_tokens: set[str],
) -> float:
    if not skills:
        return 0.0
    matched = 0
    for skill in skills:
        normalized_skill = normalize_text(skill)
        tokens = set(tokenize(skill))
        if normalized_skill in normalized_resume or (tokens and tokens <= resume_tokens):
            matched += 1
    return matched / len(skills)


def bm25f_coverage(
    resume_counts: Counter[str],
    fields: tuple[tuple[list[str], float], ...],
    *,
    saturation: float = 1.2,
) -> float:
    """Return normalized field-weighted BM25-style coverage in the 0..1 range."""
    non_empty_fields = [(tokens, weight) for tokens, weight in fields if tokens]
    if not resume_counts or not non_empty_fields:
        return 0.0
    field_sets = [set(tokens) for tokens, _ in non_empty_fields]
    field_count = len(field_sets)
    numerator = 0.0
    denominator = 0.0
    for tokens, field_weight in non_empty_fields:
        for token in set(tokens):
            document_frequency = sum(token in field for field in field_sets)
            inverse_frequency = math.log(
                1 + (field_count - document_frequency + 0.5) / (document_frequency + 0.5)
            )
            maximum = field_weight * inverse_frequency
            denominator += maximum
            term_frequency = resume_counts.get(token, 0)
            if term_frequency:
                numerator += maximum * min(
                    1.0,
                    term_frequency * (saturation + 1) / (term_frequency + saturation),
                )
    return min(1.0, numerator / denominator) if denominator else 0.0
