from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from hh_raiser.activities.vacancy_responder import respond_to_vacancy
from hh_raiser.domain.result import ActivityStatus
from hh_raiser.domain.vacancy_response import ManualResponseReason


class VacancyResponderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.page = Mock()
        self.page.url = "https://hh.ru/vacancy/123"
        self.page.locator.return_value.first = self.page.locator.return_value

    def test_stops_before_required_cover_letter(self) -> None:
        response = Mock(ok=True)
        response.json.return_value = {
            "has_test": False,
            "response_letter_required": True,
            "response_url": None,
        }
        self.page.request.get.return_value = response

        with (
            patch("hh_raiser.activities.vacancy_responder.dismiss_hh_pro_modal"),
            patch(
                "hh_raiser.activities.vacancy_responder._existing_response_visible",
                return_value=False,
            ),
        ):
            result = respond_to_vacancy(
                self.page,
                vacancy_url="https://hh.ru/vacancy/123",
                vacancy_title="Python developer",
            )

        self.assertEqual(result.status, ActivityStatus.SKIPPED)
        self.assertEqual(result.metadata["response_status"], "manual_required")
        self.assertEqual(result.metadata["manual_reason"], "cover_letter")
        self.page.locator.return_value.click.assert_not_called()

    def test_stops_before_external_destination(self) -> None:
        button = Mock()
        button.first = button
        button.get_attribute.return_value = "https://jobs.example.org/form"
        self.page.locator.return_value = button

        with (
            patch("hh_raiser.activities.vacancy_responder.dismiss_hh_pro_modal"),
            patch(
                "hh_raiser.activities.vacancy_responder._existing_response_visible",
                return_value=False,
            ),
            patch(
                "hh_raiser.activities.vacancy_responder._read_public_response_requirements",
                return_value=None,
            ),
            patch("hh_raiser.activities.vacancy_responder._visible", return_value=True),
        ):
            result = respond_to_vacancy(
                self.page,
                vacancy_url="https://hh.ru/vacancy/123",
                vacancy_title="Python developer",
            )

        self.assertEqual(result.metadata["manual_reason"], "external_site")
        button.click.assert_not_called()

    def test_existing_response_from_public_data_is_not_clicked_again(self) -> None:
        response = Mock(ok=True)
        response.json.return_value = {"relations": ["got_response"]}
        self.page.request.get.return_value = response

        with (
            patch("hh_raiser.activities.vacancy_responder.dismiss_hh_pro_modal"),
            patch(
                "hh_raiser.activities.vacancy_responder._existing_response_visible",
                return_value=False,
            ),
        ):
            result = respond_to_vacancy(
                self.page,
                vacancy_url="https://hh.ru/vacancy/123",
                vacancy_title="Python developer",
            )

        self.assertEqual(result.metadata["response_status"], "already_sent")
        self.page.locator.return_value.click.assert_not_called()

    def test_questionnaire_after_click_is_left_for_candidate(self) -> None:
        button = Mock()
        button.first = button
        button.get_attribute.return_value = "/applicant/vacancy_response?vacancyId=123"
        self.page.locator.return_value = button

        with (
            patch("hh_raiser.activities.vacancy_responder.dismiss_hh_pro_modal"),
            patch(
                "hh_raiser.activities.vacancy_responder._existing_response_visible",
                side_effect=[False, False],
            ),
            patch(
                "hh_raiser.activities.vacancy_responder._read_public_response_requirements",
                return_value=None,
            ),
            patch(
                "hh_raiser.activities.vacancy_responder._read_manual_requirement",
                return_value=(
                    ManualResponseReason.QUESTIONNAIRE,
                    "Нужно заполнить анкету.",
                ),
            ),
            patch("hh_raiser.activities.vacancy_responder._visible", return_value=True),
        ):
            result = respond_to_vacancy(
                self.page,
                vacancy_url="https://hh.ru/vacancy/123",
                vacancy_title="Python developer",
            )

        button.click.assert_called_once()
        self.assertEqual(result.metadata["response_status"], "manual_required")
        self.assertEqual(result.metadata["manual_reason"], "questionnaire")

    def test_records_success_only_after_visible_confirmation(self) -> None:
        button = Mock()
        button.first = button
        button.get_attribute.return_value = "/applicant/vacancy_response?vacancyId=123"
        self.page.locator.return_value = button

        with (
            patch("hh_raiser.activities.vacancy_responder.dismiss_hh_pro_modal"),
            patch(
                "hh_raiser.activities.vacancy_responder._existing_response_visible",
                side_effect=[False, True],
            ),
            patch(
                "hh_raiser.activities.vacancy_responder._read_public_response_requirements",
                return_value=None,
            ),
            patch("hh_raiser.activities.vacancy_responder._visible", return_value=True),
        ):
            result = respond_to_vacancy(
                self.page,
                vacancy_url="https://hh.ru/vacancy/123",
                vacancy_title="Python developer",
            )

        button.click.assert_called_once()
        self.assertEqual(result.status, ActivityStatus.SUCCESS)
        self.assertEqual(result.metadata["response_status"], "sent")
