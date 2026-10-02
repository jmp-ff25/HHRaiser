"""Paid vacancy assessment through the Polza OpenAI-compatible API."""

from __future__ import annotations

import json

from openai import OpenAI

from hh_raiser.domain.matching import InvalidModelResponse, ModelDecision, VacancyDocument

DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
DEFAULT_API_URL = "https://polza.ai/api/v1"

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


class PolzaVacancyClient:
    """Transport and response validation; no browser or storage concerns."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        api_url: str = DEFAULT_API_URL,
        client: OpenAI | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Задайте HHRAISER_MATCHING_API_KEY в .env для оценки вакансий.")
        self.model = model
        self._client = client or OpenAI(
            api_key=api_key,
            base_url=api_url,
            timeout=90,
            max_retries=0,
        )

    def evaluate(
        self, *, resume_title: str, resume_text: str, vacancy: VacancyDocument, prompt: str
    ) -> ModelDecision:
        payload = json.dumps(
            {
                "resume_title": resume_title,
                "resume": resume_text[:16_000],
                "vacancy_title": vacancy.title,
                "vacancy_description": vacancy.description[:12_000],
                "vacancy_skills": vacancy.skills,
            },
            ensure_ascii=False,
        )
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": prompt}, {"role": "user", "content": payload}],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "vacancy_match",
                    "strict": True,
                    "schema": _RESPONSE_SCHEMA,
                },
            },
            temperature=0,
            max_tokens=1000,
            extra_body={"reasoning": {"enabled": False}},
        )
        try:
            choice = response.choices[0]
            answer = json.loads(choice.message.content or "")
            if (
                choice.finish_reason != "stop"
                or not isinstance(answer, dict)
                or answer.get("verdict") not in {"fit", "unsure", "unfit"}
                or not isinstance(answer.get("reason"), str)
                or not answer["reason"].strip()
                or not isinstance(answer.get("gaps"), list)
                or any(not isinstance(item, str) for item in answer["gaps"])
            ):
                raise ValueError("invalid schema or unfinished answer")
        except (ValueError, TypeError, KeyError, IndexError, AttributeError) as error:
            raise InvalidModelResponse("Polza вернула неполную оценку вакансии") from error
        usage = response.usage
        return ModelDecision(
            verdict=answer["verdict"],
            reason=answer["reason"].strip()[:1000],
            gaps=tuple(item.strip()[:200] for item in answer["gaps"][:3] if item.strip()),
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            cost_rub=getattr(usage, "cost_rub", None),
        )
