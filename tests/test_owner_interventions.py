from __future__ import annotations

import configparser
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from hh_raiser.env_file import load_env_file
from hh_raiser.infrastructure.browser.captcha_answer_source import (
    CaptchaRequest,
    CaptchaSolutione,
)
from hh_raiser.infrastructure.browser.captcha_guard import (
    CaptchaGuard,
    ManualCaptchaGuard,
    ManualCaptchaRequired,
    captcha_controls,
)
from hh_raiser.infrastructure.storage.owner_interventions import OwnerInterventionStore

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INTEGRATION_CONFIG_PATH = PROJECT_ROOT / "state" / "main" / "hh-config.ini"
INTEGRATION_CONFIG_PATH = Path(
    os.environ.get("HHRAISER_CONFIG_FILE", DEFAULT_INTEGRATION_CONFIG_PATH)
)
load_env_file(PROJECT_ROOT / ".env")


def _captcha_integration_enabled(config_path: Path) -> bool:
    """Разрешить платный интеграционный тест только явной настройкой."""
    parser = configparser.RawConfigParser(interpolation=None)
    try:
        with config_path.open(encoding="utf-8") as stream:
            parser.read_file(stream)
        return parser.getboolean("captchasolution", "integration_test_enabled", fallback=False)
    except (OSError, configparser.Error, ValueError):
        return False


class CaptchaIntegrationConfigTests(unittest.TestCase):
    def test_integration_test_is_disabled_by_default(self) -> None:
        with TemporaryDirectory() as directory:
            self.assertFalse(_captcha_integration_enabled(Path(directory) / "hh-config.ini"))

    def test_integration_test_requires_explicit_enablement(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "hh-config.ini"
            config_path.write_text(
                "[captchasolution]\nintegration_test_enabled = true\n",
                encoding="utf-8",
            )

            self.assertTrue(_captcha_integration_enabled(config_path))


class OwnerInterventionStoreTests(unittest.TestCase):
    def test_delivers_reply_only_to_matching_pending_challenge(self) -> None:
        with TemporaryDirectory() as directory:
            store = OwnerInterventionStore(Path(directory))
            screenshot = store.screenshot_dir / "captcha-one.png"
            screenshot.write_bytes(b"png")
            store.create_captcha("one", screenshot, "Введите текст")

            pending = store.pending_for_user(100)
            self.assertEqual([item.challenge_id for item in pending], ["one"])
            store.mark_notified("one", 100, 55)

            self.assertEqual(store.pending_for_user(100), [])
            self.assertFalse(store.submit_reply(200, 55, "чужой ответ"))
            self.assertTrue(store.submit_reply(100, 55, "верный ответ"))
            self.assertFalse(store.submit_reply(100, 55, "повтор"))
            self.assertEqual(store.take_answer("one"), "верный ответ")
            self.assertIsNone(store.take_answer("one"))

            store.finish("one", "resolved")
            self.assertFalse(screenshot.exists())

    def test_each_allowed_owner_receives_the_same_pending_challenge_once(self) -> None:
        with TemporaryDirectory() as directory:
            store = OwnerInterventionStore(Path(directory))
            screenshot = store.screenshot_dir / "captcha-two.png"
            screenshot.write_bytes(b"png")
            store.create_captcha("two", screenshot, "Введите текст")

            store.mark_notified("two", 100, 10)

            self.assertEqual(store.pending_for_user(100), [])
            self.assertEqual(
                [item.challenge_id for item in store.pending_for_user(200)],
                ["two"],
            )

            store.cancel_pending()
            self.assertEqual(store.pending_for_user(200), [])
            self.assertFalse(screenshot.exists())


class CaptchaSolutioneTests(unittest.TestCase):
    def test_returns_text_from_gemini_json_response(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "hh-config.ini"
            config_path.write_text(
                "[captchasolution]\n"
                "polza_api_key = test-key\n"
                "ocr_prompt = Точно перепиши CAPTCHA\n"
                'api_url = "https://polza.ai/api/v1"\n'
                'model = "google/gemini-3.8-flash"\n',
                encoding="utf-8",
            )
            image = np.full((20, 40, 3), 255, dtype=np.uint8)
            succeeded, buffer = cv2.imencode(".png", image)
            self.assertTrue(succeeded)

            with patch("hh_raiser.infrastructure.browser.captcha_answer_source.OpenAI") as openai:
                response = openai.return_value.chat.completions.create.return_value
                response.choices = [MagicMock(message=MagicMock(content='{"text":"Верный ответ"}'))]
                answer = CaptchaSolutione(config_path).get_answer(
                    CaptchaRequest("one", buffer.tobytes(), "Введите текст")
                )

        self.assertEqual(answer, "верный ответ")


class GeminiCaptchaFallbackTests(unittest.TestCase):
    def test_tries_gemini_five_times_before_manual_fallback(self) -> None:
        with TemporaryDirectory() as directory:
            source = MagicMock()
            source.get_answer.side_effect = ["ответ"] * 5
            guard = CaptchaGuard(OwnerInterventionStore(Path(directory)), answer_source=source)
            page = MagicMock()
            page.is_closed.return_value = False
            image, input_field, submit = MagicMock(), MagicMock(), MagicMock()

            with (
                patch(
                    "hh_raiser.infrastructure.browser.captcha_guard.CaptchaGuard.is_present",
                    return_value=True,
                ),
                patch(
                    "hh_raiser.infrastructure.browser.captcha_guard.captcha_controls",
                    return_value=(image, input_field, submit),
                ),
                patch.object(
                    guard, "_wait_for_result", side_effect=[False, False, False, False, False]
                ),
            ):
                self.assertFalse(guard._try_gemini_answers(page, lambda: False))

        self.assertEqual(source.get_answer.call_count, 5)
        self.assertEqual(input_field.fill.call_count, 5)

    def test_manual_fallback_does_not_call_gemini_after_five_attempts(self) -> None:
        with TemporaryDirectory() as directory:
            source = MagicMock()
            guard = CaptchaGuard(OwnerInterventionStore(Path(directory)), answer_source=source)
            page = MagicMock()
            page.is_closed.return_value = False
            image, input_field, submit = MagicMock(), MagicMock(), MagicMock()
            image.screenshot.return_value = b"png"

            with (
                patch.object(guard, "_try_gemini_answers", return_value=False),
                patch(
                    "hh_raiser.infrastructure.browser.captcha_guard.CaptchaGuard.is_present",
                    return_value=True,
                ),
                patch(
                    "hh_raiser.infrastructure.browser.captcha_guard.captcha_controls",
                    return_value=(image, input_field, submit),
                ),
                patch.object(guard, "_wait_for_answer", return_value="ручной ответ"),
                patch.object(guard, "_wait_for_result", return_value=True),
            ):
                self.assertTrue(guard.resolve_if_present(page))

        source.get_answer.assert_not_called()

    def test_missing_config_logs_error_and_returns_no_answer(self) -> None:
        with (
            TemporaryDirectory() as directory,
            patch(
                "hh_raiser.infrastructure.browser.captcha_answer_source.LOGGER.error"
            ) as log_error,
        ):
            source = CaptchaSolutione(Path(directory) / "missing.ini")

        self.assertIsNone(
            source.get_answer(CaptchaRequest("one", b"not-an-image", "Введите текст"))
        )
        log_error.assert_called_once()

    def test_visible_browser_tries_gemini_five_times_before_manual_fallback(self) -> None:
        source = MagicMock()
        source.get_answer.side_effect = ["ответ"] * 5
        page = MagicMock()
        page.is_closed.return_value = False
        image, input_field, submit = MagicMock(), MagicMock(), MagicMock()
        image.screenshot.return_value = b"png"
        guard = ManualCaptchaGuard(headless=False, answer_source=source)

        with (
            patch(
                "hh_raiser.infrastructure.browser.captcha_guard.CaptchaGuard.is_present",
                side_effect=[True, True, True, True, True, True, False],
            ),
            patch(
                "hh_raiser.infrastructure.browser.captcha_guard.captcha_controls",
                return_value=(image, input_field, submit),
            ),
            patch(
                "hh_raiser.infrastructure.browser.captcha_guard._wait_for_captcha_result",
                return_value=False,
            ),
        ):
            self.assertTrue(guard.resolve_if_present(page))

        self.assertEqual(source.get_answer.call_count, 5)
        self.assertEqual(input_field.fill.call_count, 5)

    def test_headless_browser_tries_gemini_before_manual_error(self) -> None:
        source = MagicMock()
        source.get_answer.side_effect = ["ответ"] * 5
        page = MagicMock()
        page.is_closed.return_value = False
        image, input_field, submit = MagicMock(), MagicMock(), MagicMock()
        image.screenshot.return_value = b"png"
        guard = ManualCaptchaGuard(headless=True, answer_source=source)

        with (
            patch(
                "hh_raiser.infrastructure.browser.captcha_guard.CaptchaGuard.is_present",
                side_effect=[True, True, True, True, True, True],
            ),
            patch(
                "hh_raiser.infrastructure.browser.captcha_guard.captcha_controls",
                return_value=(image, input_field, submit),
            ),
            patch(
                "hh_raiser.infrastructure.browser.captcha_guard._wait_for_captcha_result",
                return_value=False,
            ),
            self.assertRaisesRegex(ManualCaptchaRequired, "без окна"),
        ):
            guard.resolve_if_present(page)

        self.assertEqual(source.get_answer.call_count, 5)
        self.assertEqual(input_field.fill.call_count, 5)

    @unittest.skipUnless(
        _captcha_integration_enabled(INTEGRATION_CONFIG_PATH),
        "Установите [captchasolution] integration_test_enabled = true в state/main/hh-config.ini "
        "для платного запроса Gemini.",
    )
    def test_recognizes_real_captcha_with_gemini(self) -> None:
        image_path = PROJECT_ROOT / "tests" / "fixtures" / "captcha_gemini_integration.jpg"
        answer = CaptchaSolutione(INTEGRATION_CONFIG_PATH).get_answer(
            CaptchaRequest("integration", image_path.read_bytes(), "Введите текст")
        )

        self.assertEqual(answer, "евшему увидала")


class CaptchaRecognitionTests(unittest.TestCase):
    def test_recognizes_hh_captcha_by_stable_path(self) -> None:
        page = MagicMock()
        page.url = "https://hh.ru/account/captcha?backurl=private"

        self.assertTrue(CaptchaGuard.is_present(page))
        page.get_by_role.assert_not_called()

    def test_does_not_treat_vacancy_title_as_captcha_without_heading(self) -> None:
        page = MagicMock()
        page.url = "https://hh.ru/vacancy/123"
        heading = page.get_by_role.return_value.first
        heading.count.return_value = 0

        self.assertFalse(CaptchaGuard.is_present(page))

    def test_uses_accessible_captcha_controls_with_stable_fallbacks(self) -> None:
        page = MagicMock()
        role_image = MagicMock()
        role_input = MagicMock()
        role_submit = MagicMock()
        combined_image = MagicMock()
        combined_input = MagicMock()
        combined_submit = MagicMock()
        page.get_by_role.side_effect = [role_image, role_input, role_submit]
        role_image.or_.return_value = combined_image
        role_input.or_.return_value = combined_input
        role_submit.or_.return_value = combined_submit

        controls = captcha_controls(page)

        self.assertEqual(
            controls,
            (combined_image.first, combined_input.first, combined_submit.first),
        )
        page.get_by_role.assert_any_call("img", name="captcha", exact=True)
        page.get_by_role.assert_any_call(
            "textbox",
            name="Текст с картинки",
            exact=True,
        )
        page.get_by_role.assert_any_call("button", name="Отправить", exact=True)
        selectors = [call.args[0] for call in page.locator.call_args_list]
        self.assertEqual(
            selectors,
            [
                '[data-qa="account-captcha-picture"]',
                '[data-qa="account-captcha-input"]',
                '[data-qa="account-captcha-submit"]',
            ],
        )

    def test_visible_browser_waits_for_owner_to_complete_captcha(self) -> None:
        page = MagicMock()
        page.is_closed.return_value = False
        guard = ManualCaptchaGuard(headless=False)

        with patch(
            "hh_raiser.infrastructure.browser.captcha_guard.CaptchaGuard.is_present",
            side_effect=[True, True, False],
        ):
            self.assertTrue(guard.resolve_if_present(page))

        page.wait_for_timeout.assert_called_once()

    def test_headless_browser_stops_for_manual_captcha(self) -> None:
        page = MagicMock()
        guard = ManualCaptchaGuard(headless=True)

        with (
            patch(
                "hh_raiser.infrastructure.browser.captcha_guard.CaptchaGuard.is_present",
                return_value=True,
            ),
            self.assertRaisesRegex(ManualCaptchaRequired, "без окна"),
        ):
            guard.resolve_if_present(page)


if __name__ == "__main__":
    unittest.main()
