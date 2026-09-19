from __future__ import annotations

import argparse
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from hh_raiser.credentials import (
    normalize_russian_phone,
    parse_ini_credentials,
    read_credentials_file,
    resolve_credentials,
)
from hh_raiser.models import Credentials


class CredentialsTests(unittest.TestCase):
    def test_ini_keeps_password_special_characters(self) -> None:
        credentials = parse_ini_credentials(
            '[hh]\nphone = +79990000000\npassword = pa"ss\\word=42%\n'
        )
        self.assertEqual(credentials, Credentials(phone="+79990000000", password='pa"ss\\word=42%'))

    def test_command_line_credentials_take_precedence(self) -> None:
        args = argparse.Namespace(phone="+79990000000", password="secret", credentials_file=None)
        self.assertEqual(
            resolve_credentials(args), Credentials(phone="+79990000000", password="secret")
        )

    def test_credentials_file_supplies_phone_and_password(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.ini"
            path.write_text("[hh]\nphone = +79990000000\npassword = secret\n", encoding="utf-8")
            credentials = read_credentials_file(path)
        self.assertEqual(credentials, Credentials(phone="+79990000000", password="secret"))

    def test_missing_credentials_file_explains_how_to_fix_it(self) -> None:
        missing_path = Path("missing-hh-credentials.ini")

        with self.assertRaisesRegex(
            ValueError,
            "Файл учётных данных не найден.*--credentials-file",
        ):
            read_credentials_file(missing_path)

    def test_normalizes_russian_phone(self) -> None:
        self.assertEqual(normalize_russian_phone("+7(991) 174-28-79"), "9911742879")

    def test_missing_credentials_never_requests_interactive_input(self) -> None:
        args = argparse.Namespace(phone=None, password=None, credentials_file=None)

        with self.assertRaisesRegex(ValueError, "HH_PHONE и HH_PASSWORD"):
            resolve_credentials(args)
