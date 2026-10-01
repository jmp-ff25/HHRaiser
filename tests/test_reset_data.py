from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from hh_raiser import reset_data as reset_module
from hh_raiser.reset_data import reset_data


class ResetDataTests(unittest.TestCase):
    def test_preview_and_reset_preserve_configuration_and_secrets(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pyproject.toml").touch()
            (root / "Taskfile.yml").touch()
            (root / ".env").write_text("SECRET=value\n", encoding="utf-8")
            state = root / "state" / "main"
            state.mkdir(parents=True)
            legacy = root / ".hh-resume-raiser"
            legacy.mkdir()
            preserved = [
                root / ".env",
                state / "hh-config.ini",
                state / "extra.toml",
                state / ".env.local",
            ]
            removed = [
                state / "vacancy-history.sqlite3",
                state / "activity-events.jsonl",
                state / "browser-profile" / "Cookies",
                state / "ollama-models" / "model.bin",
                legacy / "status.json",
                root / "vacancy-responses.xlsx",
            ]
            for path in [*preserved[1:], *removed]:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("example", encoding="utf-8")

            preview = reset_data(root, dry_run=True, check_services=False)
            self.assertEqual(set(preview), set(removed))
            self.assertTrue(all(path.exists() for path in removed))

            actual = reset_data(root, check_services=False)
            self.assertEqual(set(actual), set(removed))
            self.assertTrue(all(path.exists() for path in preserved))
            self.assertTrue(all(not path.exists() for path in removed))
            self.assertFalse((state / "browser-profile").exists())

    def test_rejects_non_project_directory(self) -> None:
        with (
            TemporaryDirectory() as directory,
            self.assertRaisesRegex(ValueError, "Не найден корень проекта"),
        ):
            reset_data(Path(directory), check_services=False)

    def test_stops_only_own_portable_ollama_process(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            portable = root / "state" / "main" / "local-ollama" / "ollama.exe"
            portable.parent.mkdir(parents=True)
            portable.touch()
            (root / "state" / "main" / "ollama-server.pid").write_text("123", encoding="ascii")
            with patch.object(reset_module.subprocess, "run") as run:
                run.side_effect = [Mock(stdout=str(portable)), Mock(), Mock(stdout=b"")]
                reset_module._stop_project_ollama(root)
            self.assertEqual(run.call_count, 3)
            self.assertIn("Stop-Process -Id 123", run.call_args_list[1].args[0][-1])

    def test_rejects_active_server_instance_before_cleanup(self) -> None:
        with (
            patch.object(reset_module.sys, "platform", "linux"),
            patch.object(reset_module.shutil, "which", return_value="/usr/bin/systemctl"),
            patch.object(reset_module.subprocess, "run") as run,
        ):
            run.return_value = Mock(
                returncode=0, stdout="hhraiser@other.service loaded active running\n"
            )
            with self.assertRaisesRegex(RuntimeError, "hhraiser@other.service"):
                reset_module._ensure_services_stopped()


if __name__ == "__main__":
    unittest.main()
