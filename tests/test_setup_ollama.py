from __future__ import annotations

import io
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from hh_raiser import setup_ollama


class SetupOllamaTests(unittest.TestCase):
    def test_uses_configured_model_and_default(self) -> None:
        with TemporaryDirectory() as directory:
            config_file = Path(directory) / "hh-config.ini"
            self.assertEqual(setup_ollama.configured_model(config_file), "qwen3:1.7b")
            config_file.write_text("[matching]\nlocal_model = custom:latest\n", encoding="utf-8")
            self.assertEqual(setup_ollama.configured_model(config_file), "custom:latest")

    def test_setup_installs_missing_ollama_and_pulls_configured_model(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_file = root / "hh-config.ini"
            config_file.write_text("[matching]\nlocal_model = qwen3:1.7b\n", encoding="utf-8")
            executable = root / "ollama.exe"
            with (
                patch.object(setup_ollama, "find_ollama", side_effect=[None, executable]) as find,
                patch.object(setup_ollama, "install_ollama") as install,
                patch.object(setup_ollama, "start_ollama") as start,
                patch.object(setup_ollama, "ensure_model") as ensure,
            ):
                result = setup_ollama.main(
                    ["--project-dir", str(root), "--config-file", str(config_file)]
                )
            self.assertEqual(result, 0)
            self.assertEqual(find.call_count, 2)
            install.assert_called_once_with()
            start.assert_called_once_with(executable, root)
            ensure.assert_called_once_with(executable, "qwen3:1.7b")

    def test_existing_model_skips_pull_and_missing_model_is_verified(self) -> None:
        executable = Path("ollama")
        with patch.object(setup_ollama.subprocess, "run") as run:
            run.return_value = Mock(returncode=0)
            setup_ollama.ensure_model(executable, "qwen3:1.7b")
            self.assertEqual(run.call_count, 2)
            self.assertFalse(any("pull" in invocation.args[0] for invocation in run.call_args_list))

            run.reset_mock()
            run.side_effect = [Mock(returncode=1), Mock(returncode=0), Mock(returncode=0)]
            setup_ollama.ensure_model(executable, "qwen3:1.7b")
            self.assertEqual(
                [invocation.args[0][1] for invocation in run.call_args_list],
                ["show", "pull", "show"],
            )

    def test_failed_download_does_not_report_model_ready(self) -> None:
        with patch.object(setup_ollama.subprocess, "run") as run:
            run.side_effect = [
                Mock(returncode=1),
                subprocess.CalledProcessError(1, ["ollama", "pull"]),
            ]
            with self.assertRaises(subprocess.CalledProcessError):
                setup_ollama.ensure_model(Path("ollama"), "qwen3:1.7b")

    def test_windows_install_uses_signed_official_exe_without_powershell(self) -> None:
        with (
            patch.object(setup_ollama.sys, "platform", "win32"),
            patch.object(
                setup_ollama, "urlopen", return_value=io.BytesIO(b"signed installer")
            ) as get,
            patch.object(setup_ollama, "verify_windows_signature") as verify,
            patch.object(setup_ollama.subprocess, "run") as run,
        ):
            setup_ollama.install_ollama()
        get.assert_called_once_with(setup_ollama.WINDOWS_INSTALLER_URL, timeout=60)
        verify.assert_called_once()
        command = run.call_args.args[0]
        self.assertEqual(Path(command[0]).name, "OllamaSetup.exe")
        self.assertEqual(command[1:], ["/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES"])


if __name__ == "__main__":
    unittest.main()
