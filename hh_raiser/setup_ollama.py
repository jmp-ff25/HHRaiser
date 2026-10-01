"""Install Ollama and the configured local matching model during project setup."""

from __future__ import annotations

import argparse
import configparser
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

MODEL_DEFAULT = "qwen3:1.7b"
API_TAGS_URL = "http://127.0.0.1:11434/api/tags"
INSTALL_URL = (
    "https://ollama.com/install.ps1" if sys.platform == "win32" else "https://ollama.com/install.sh"
)


def configured_model(config_file: Path) -> str:
    parser = configparser.RawConfigParser(interpolation=None)
    if config_file.is_file():
        with config_file.open(encoding="utf-8") as stream:
            parser.read_file(stream)
    model = parser.get("matching", "local_model", fallback=MODEL_DEFAULT).strip()
    if not model or len(model) > 200:
        raise ValueError("matching.local_model должен содержать от 1 до 200 символов")
    return model


def find_ollama(project_dir: Path) -> Path | None:
    portable = project_dir / "state" / "main" / "local-ollama" / "ollama.exe"
    if sys.platform == "win32" and portable.is_file():
        return portable
    executable = shutil.which("ollama")
    if executable:
        return Path(executable)
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            installed = Path(local_app_data) / "Programs" / "Ollama" / "ollama.exe"
            if installed.is_file():
                return installed
    return None


def install_ollama() -> None:
    """Run Ollama's official platform installer only when its CLI is missing."""
    print("Ollama не найдена; устанавливаю из официального источника.", flush=True)
    with tempfile.TemporaryDirectory(prefix="hhraiser-ollama-") as directory:
        script = Path(directory) / ("install.ps1" if sys.platform == "win32" else "install.sh")
        with urlopen(INSTALL_URL, timeout=30) as response, script.open("wb") as output:
            shutil.copyfileobj(response, output)
        command = (
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]
            if sys.platform == "win32"
            else ["sh", str(script)]
        )
        subprocess.run(command, check=True)


def api_ready() -> bool:
    try:
        with urlopen(API_TAGS_URL, timeout=2) as response:
            return isinstance(json.load(response).get("models"), list)
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def start_ollama(executable: Path, project_dir: Path) -> None:
    # The Windows installer may launch the desktop service a moment after it exits.
    if _wait_for_api(10):
        return
    if sys.platform == "linux" and shutil.which("systemctl"):
        unit = subprocess.run(
            ["systemctl", "cat", "ollama.service"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if unit.returncode == 0:
            command = ["systemctl", "enable", "--now", "ollama.service"]
            if os.geteuid() != 0 and shutil.which("sudo"):
                command.insert(0, "sudo")
            subprocess.run(command, check=True)
            if _wait_for_api(60):
                return
            raise RuntimeError("Служба Ollama запущена, но API на 127.0.0.1:11434 недоступен")

    state_dir = project_dir / "state" / "main"
    state_dir.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["OLLAMA_HOST"] = "127.0.0.1:11434"
    if executable == state_dir / "local-ollama" / "ollama.exe":
        models = state_dir / "ollama-models"
        models.mkdir(parents=True, exist_ok=True)
        environment["OLLAMA_MODELS"] = str(models)
    log_path = state_dir / "ollama-server.log"
    with log_path.open("ab") as log:
        options: dict[str, object] = {
            "env": environment,
            "stdin": subprocess.DEVNULL,
            "stdout": log,
            "stderr": subprocess.STDOUT,
        }
        if sys.platform == "win32":
            options["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
        else:
            options["start_new_session"] = True
        process = subprocess.Popen([str(executable), "serve"], **options)
    (state_dir / "ollama-server.pid").write_text(str(process.pid), encoding="ascii")
    if not _wait_for_api(60):
        raise RuntimeError("Ollama не запустилась; проверьте state/main/ollama-server.log")


def _wait_for_api(seconds: int) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if api_ready():
            return True
        time.sleep(1)
    return api_ready()


def ensure_model(executable: Path, model: str) -> None:
    environment = os.environ.copy()
    environment["OLLAMA_HOST"] = "127.0.0.1:11434"
    check = subprocess.run(
        [str(executable), "show", model],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if check.returncode != 0:
        print(f"Скачиваю модель Ollama {model}...", flush=True)
        subprocess.run([str(executable), "pull", model], env=environment, check=True)
    subprocess.run(
        [str(executable), "show", model],
        env=environment,
        stdout=subprocess.DEVNULL,
        check=True,
    )
    print(f"Модель Ollama {model} готова.", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Подготовить Ollama и локальную модель.")
    parser.add_argument("--project-dir", type=Path, default=Path.cwd())
    parser.add_argument("--config-file", type=Path)
    args = parser.parse_args(argv)
    project_dir = args.project_dir.resolve()
    config_file = args.config_file or project_dir / "state" / "main" / "hh-config.ini"
    model = configured_model(config_file)
    executable = find_ollama(project_dir)
    if executable is None:
        install_ollama()
        executable = find_ollama(project_dir)
    if executable is None:
        raise RuntimeError("Установщик завершился, но команда ollama не найдена")
    start_ollama(executable, project_dir)
    ensure_model(executable, model)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        OSError,
        configparser.Error,
        subprocess.CalledProcessError,
        ValueError,
        RuntimeError,
    ) as error:
        print(f"Подготовка Ollama не завершена: {error}", file=sys.stderr)
        raise SystemExit(1) from error
