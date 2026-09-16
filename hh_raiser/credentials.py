from __future__ import annotations

import argparse
import configparser
import getpass
import os
from pathlib import Path

from hh_raiser.models import Credentials


def parse_ini_credentials(text: str) -> Credentials:
    parser = configparser.RawConfigParser(interpolation=None)
    try:
        parser.read_string(text)
    except configparser.Error as error:
        raise ValueError("Некорректный INI-файл учётных данных") from error
    if not parser.has_section("hh"):
        raise ValueError("В INI-файле учётных данных отсутствует секция [hh]")
    phone = parser.get("hh", "phone", fallback="").strip()
    password = parser.get("hh", "password", fallback="")
    if not phone:
        raise ValueError("В секции [hh] отсутствует непустая строка phone=...")
    if not password:
        raise ValueError("В секции [hh] отсутствует непустая строка password=...")
    return Credentials(phone=phone, password=password)


def read_credentials_file(path: Path) -> Credentials:
    if not path.exists():
        raise ValueError(
            f"Файл учётных данных не найден: {path}. "
            "Добавьте файл в папку проекта или укажите путь через --credentials-file."
        )
    if not path.is_file():
        raise ValueError(f"Путь к файлу учётных данных не является файлом: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(f"Не удалось прочитать файл учётных данных: {path}") from error
    return parse_ini_credentials(text)


def resolve_credentials(args: argparse.Namespace) -> Credentials:
    from_file = getattr(args, "credentials_from_file", None)
    if from_file is None and args.credentials_file:
        from_file = read_credentials_file(args.credentials_file)
    phone = args.phone or os.environ.get("HH_PHONE") or (from_file.phone if from_file else None)
    password = (
        args.password
        or os.environ.get("HH_PASSWORD")
        or (from_file.password if from_file else None)
    )
    if bool(phone) != bool(password):
        raise ValueError("Телефон и пароль нужно передать вместе")
    if phone and password:
        return Credentials(phone=phone, password=password)
    return Credentials(
        phone=input("Телефон HH: ").strip(),
        password=getpass.getpass("Пароль HH: "),
    )


def normalize_russian_phone(phone: str) -> str:
    digits = "".join(character for character in phone if character.isdigit())
    if len(digits) == 11 and digits[0] in {"7", "8"}:
        digits = digits[1:]
    if len(digits) != 10:
        raise ValueError("Для входа в HH нужен российский номер из 10 цифр после +7")
    return digits
