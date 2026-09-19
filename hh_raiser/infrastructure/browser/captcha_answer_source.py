"""Источник автоматического ответа на CAPTCHA через Gemini."""

from __future__ import annotations

import base64
import configparser
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np
from openai import OpenAI

from hh_raiser.logging_config import LOGGER, LogEvent, event_data


@dataclass(frozen=True)
class CaptchaRequest:
    """Изображение CAPTCHA, полученное браузером."""

    challenge_id: str
    image_bytes: bytes
    prompt: str


class CaptchaAnswerSource(Protocol):
    """Контракт источника ответа на CAPTCHA."""

    def get_answer(self, request: CaptchaRequest) -> str | None:
        """Вернуть распознанный ответ или ``None``."""


class CaptchaSolutione:
    """Распознавать текст CAPTCHA через совместимый с OpenAI API."""

    REQUEST_TIMEOUT_SECONDS = 20.0
    RESPONSE_ATTEMPTS = 3

    def __init__(self, config_path: Path = Path("hh-config.ini")) -> None:
        """Создать источник из секции ``[captchasolution]`` INI-файла."""
        self.client: OpenAI | None = None
        self.model = ""
        self.prompt = ""
        parser = configparser.RawConfigParser(interpolation=None)
        try:
            with config_path.open(encoding="utf-8") as stream:
                parser.read_file(stream)
        except OSError:
            LOGGER.error(
                "Не удалось прочитать настройки CAPTCHA: %s",
                config_path,
                extra=event_data(LogEvent.CAPTCHA),
            )
            return
        except configparser.Error:
            LOGGER.error(
                "Некорректный INI-файл настроек CAPTCHA: %s",
                config_path,
                extra=event_data(LogEvent.CAPTCHA),
            )
            return

        api_key = os.environ.get("HHRAISER_POLZA_API_KEY", "").strip()
        prompt = self._read_setting(parser, "ocr_prompt")
        api_url = self._read_setting(parser, "api_url")
        model = self._read_setting(parser, "model")
        if not all((api_key, prompt, api_url, model)):
            LOGGER.error(
                "Задайте HHRAISER_POLZA_API_KEY, ocr_prompt, api_url и model для CAPTCHA.",
                extra=event_data(LogEvent.CAPTCHA),
            )
            return
        self.client = OpenAI(
            api_key=api_key,
            base_url=api_url,
            timeout=self.REQUEST_TIMEOUT_SECONDS,
            max_retries=0,
        )
        self.model = model
        self.prompt = prompt

    @staticmethod
    def _read_setting(parser: configparser.RawConfigParser, option: str) -> str:
        """Получить настройку CAPTCHA, поддерживая обычные INI-кавычки."""
        value = parser.get("captchasolution", option, fallback="").strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            return value[1:-1].strip()
        return value

    def get_answer(self, request: CaptchaRequest) -> str | None:
        image = cv2.imdecode(np.frombuffer(request.image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            LOGGER.warning(
                "Не удалось прочитать изображение CAPTCHA.",
                extra=event_data(LogEvent.CAPTCHA),
            )
            return None
        if self.client is None:
            return None

        LOGGER.info("Распознаю CAPTCHA через %s.", self.model, extra=event_data(LogEvent.CAPTCHA))
        content: list[dict[str, Any]] = [{"type": "text", "text": self.prompt}]
        for label, visual in [("Исходная CAPTCHA", image), *self._straighten_text(image)]:
            content.extend(
                [
                    {"type": "text", "text": label},
                    {"type": "image_url", "image_url": {"url": self._encode_png(visual)}},
                ]
            )

        for attempt in range(1, self.RESPONSE_ATTEMPTS + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": content}],
                    temperature=0,
                    max_tokens=1_024,
                )
                answer = self._parse_answer(response.choices[0].message.content or "")
            except Exception as error:  # noqa: BLE001 — CAPTCHA остаётся необязательной автоматизацией.
                LOGGER.warning(
                    "Запрос Gemini завершился ошибкой %s: попытка %s из %s.",
                    type(error).__name__,
                    attempt,
                    self.RESPONSE_ATTEMPTS,
                    extra=event_data(LogEvent.CAPTCHA),
                )
                continue
            if answer is not None:
                LOGGER.info(
                    "Gemini вернул ответ CAPTCHA длиной %s символов.",
                    len(answer),
                    extra=event_data(LogEvent.CAPTCHA),
                )
                return answer
            LOGGER.warning(
                "Gemini вернул некорректный ответ CAPTCHA: попытка %s из %s.",
                attempt,
                self.RESPONSE_ATTEMPTS,
                extra=event_data(LogEvent.CAPTCHA),
            )
        return None

    def _straighten_text(self, image: np.ndarray) -> list[tuple[str, np.ndarray]]:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        mask = np.full_like(gray, 255)
        mask[gray < 55] = 0
        foreground = mask < 128
        x_values = np.flatnonzero(np.any(foreground, axis=0))
        if len(x_values) < 3:
            return []
        y_values = np.array(
            [np.median(np.flatnonzero(foreground[:, x])) for x in x_values], dtype=np.float32
        )
        coefficients = self._robust_quadratic(x_values.astype(np.float32), y_values)
        baseline = np.polyval(coefficients, np.arange(mask.shape[1], dtype=np.float32))
        baseline -= float(np.median(baseline))
        coordinate_x, coordinate_y = np.meshgrid(
            np.arange(mask.shape[1], dtype=np.float32), np.arange(mask.shape[0], dtype=np.float32)
        )
        rectified = cv2.remap(
            mask,
            coordinate_x,
            (coordinate_y + baseline[np.newaxis, :]).astype(np.float32),
            interpolation=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=255,
        )
        rectified = cv2.resize(rectified, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        return [("Выпрямление всей строки", cv2.cvtColor(rectified, cv2.COLOR_GRAY2BGR))]

    @staticmethod
    def _robust_quadratic(x_values: np.ndarray, y_values: np.ndarray) -> np.ndarray:
        keep = np.ones(len(x_values), dtype=bool)
        for _ in range(3):
            coefficients = np.polyfit(x_values[keep], y_values[keep], deg=2)
            residuals = y_values - np.polyval(coefficients, x_values)
            median = float(np.median(residuals[keep]))
            deviation = float(np.median(np.abs(residuals[keep] - median)))
            keep = np.abs(residuals - median) <= max(3.0, deviation * 2.5)
        return np.polyfit(x_values[keep], y_values[keep], deg=2)

    @staticmethod
    def _encode_png(image: np.ndarray) -> str:
        succeeded, buffer = cv2.imencode(".png", image)
        if not succeeded:
            raise RuntimeError("Не удалось подготовить CAPTCHA для API.")
        return f"data:image/png;base64,{base64.b64encode(buffer.tobytes()).decode('ascii')}"

    @classmethod
    def _parse_answer(cls, answer: str) -> str | None:
        """Извлечь текст из полного или усечённого JSON-ответа модели."""
        complete_json = re.search(r"\{.*\}", answer, flags=re.DOTALL)
        if complete_json:
            try:
                text = cls._normalize_text(str(json.loads(complete_json.group()).get("text", "")))
                return text or None
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        partial = re.search(r'"text"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', answer)
        return cls._normalize_text(partial.group(1)) if partial else None

    @staticmethod
    def _normalize_text(text: str) -> str:
        """Нормализовать пробелы и регистр в ответе модели."""
        return " ".join(text.lower().strip().split())
