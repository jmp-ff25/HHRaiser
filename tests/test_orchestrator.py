from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from hh_raiser.application.orchestrator import ActivityOrchestrator


class ActivityOrchestratorTests(unittest.TestCase):
    def test_passes_history_before_resume_title_to_group_runner(self) -> None:
        page = Mock()
        policy = Mock()
        traversal = Mock()
        rotation = Mock()
        history = Mock()
        orchestrator = ActivityOrchestrator(
            policy=policy,
            report_path=Path("activity.jsonl"),
            rotation=rotation,
            history=history,
            resume_title="Backend developer",
            traversal=traversal,
        )

        with (
            patch(
                "hh_raiser.application.orchestrator.run_vacancy_page_group",
                return_value=[],
            ) as run_group,
            patch("hh_raiser.application.orchestrator.append_activity_results"),
        ):
            orchestrator.run(page)

        run_group.assert_called_once_with(
            page,
            policy,
            traversal,
            history,
            "Backend developer",
            captcha_guard=None,
            stop_requested=None,
        )


if __name__ == "__main__":
    unittest.main()
