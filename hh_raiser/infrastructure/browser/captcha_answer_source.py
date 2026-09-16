"""Точка расширения для ручного ответа владельца через личный внешний интерфейс."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CaptchaRequest:
    """Обезличенный запрос с изображением CAPTCHA для ручного ввода владельцем."""

    challenge_id: str
    image_bytes: bytes
    prompt: str


class CaptchaAnswerSource(Protocol):
    """Источник ответа, который не должен автоматически распознавать CAPTCHA."""

    def get_answer(self, request: CaptchaRequest) -> str | None:
        """Вернуть введённый владельцем ответ либо ``None``, пока ответа нет."""


class WebsiteCaptchaAnswerSource:
    """Заглушка для личного сайта владельца без сетевых вызовов по умолчанию.

    Будущий клиент сайта должен передать изображение в личный интерфейс и вернуть
    введённый человеком текст. До этого метод безопасно возвращает ``None``, поэтому
    Telegram- и локальный сценарии продолжают работать как раньше.
    """

    def get_answer(self, request: CaptchaRequest) -> str | None:
        """Получить ответ с личного сайта; сетевой клиент будет добавлен позднее."""

        del request
        return None
