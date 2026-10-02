"""Remove generated HHRaiser state while preserving configuration and secrets."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

STATE_DIRECTORIES = ("state", ".hh-resume-raiser")
ROOT_RUNTIME_FILES = (
    "activity-events.jsonl",
    "vacancy-history.sqlite3",
    "vacancy-responses.xlsx",
    "owner-interventions.sqlite3",
    "status.json",
)
CONFIG_SUFFIXES = {".ini", ".toml", ".yaml", ".yml"}


def is_protected(path: Path) -> bool:
    name = path.name.casefold()
    return name == ".env" or name.startswith(".env.") or path.suffix.casefold() in CONFIG_SUFFIXES


def _ensure_safe_directory(path: Path, root: Path) -> None:
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError(f"Небезопасный каталог состояния: {path}")


def _clear_directory(path: Path, root: Path, *, dry_run: bool, removed: list[Path]) -> None:
    _ensure_safe_directory(path, root)
    for entry in os.scandir(path):
        child = Path(entry.path)
        if entry.is_symlink():
            if is_protected(child):
                continue
            removed.append(child)
            if not dry_run:
                child.unlink()
        elif entry.is_dir(follow_symlinks=False):
            _clear_directory(child, root, dry_run=dry_run, removed=removed)
            if not dry_run and not any(child.iterdir()):
                child.rmdir()
        elif not is_protected(child):
            removed.append(child)
            if not dry_run:
                child.unlink()


def _ensure_services_stopped() -> None:
    if sys.platform != "linux" or not shutil.which("systemctl"):
        return
    result = subprocess.run(
        [
            "systemctl",
            "list-units",
            "--type=service",
            "--state=active",
            "--no-legend",
            "--plain",
            "hhraiser@*.service",
            "hhraiser-bot.service",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("Не удалось проверить состояние служб HHRaiser через systemctl")
    active = [line.split()[0] for line in result.stdout.splitlines() if line.strip()]
    if active:
        raise RuntimeError("Сначала остановите работающие службы: " + ", ".join(active))


def reset_data(
    project_dir: Path, *, dry_run: bool = False, check_services: bool = True
) -> list[Path]:
    root = project_dir.resolve()
    if not (root / "pyproject.toml").is_file() or not (root / "Taskfile.yml").is_file():
        raise ValueError(f"Не найден корень проекта HHRaiser: {root}")
    if check_services:
        _ensure_services_stopped()
    removed: list[Path] = []
    for name in STATE_DIRECTORIES:
        directory = root / name
        if directory.exists() or directory.is_symlink():
            _clear_directory(directory, root, dry_run=dry_run, removed=removed)
    for name in ROOT_RUNTIME_FILES:
        path = root / name
        if path.is_file() or path.is_symlink():
            if path.parent != root:
                raise ValueError(f"Небезопасный путь: {path}")
            removed.append(path)
            if not dry_run:
                path.unlink()
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Очистить данные HHRaiser, сохранив конфиги.")
    parser.add_argument("--project-dir", type=Path, default=Path.cwd())
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    root = args.project_dir.resolve()
    removed = reset_data(root, dry_run=args.dry_run)
    action = "Найдены для удаления" if args.dry_run else "Удалены"
    print(f"{action} {len(removed)} файлов и ссылок:")
    for path in removed[:40]:
        print(f"  {path.relative_to(root)}")
    if len(removed) > 40:
        print(f"  ... и ещё {len(removed) - 40}")
    print("Файлы конфигурации и .env сохранены.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Очистка не завершена: {error}", file=sys.stderr)
        raise SystemExit(1) from error
