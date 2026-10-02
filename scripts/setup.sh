#!/usr/bin/env sh
# Локальная установка для Linux и macOS. Task должен быть установлен владельцем.
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
cd "$PROJECT_DIR"
mkdir -p "$PROJECT_DIR/state/main"
if [ ! -f "$PROJECT_DIR/.env" ]; then
  cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
fi
if [ ! -f "$PROJECT_DIR/state/main/hh-config.ini" ]; then
  cp "$PROJECT_DIR/hh-config.example.ini" "$PROJECT_DIR/state/main/hh-config.ini"
fi
export UV_CACHE_DIR="$PROJECT_DIR/.uv-cache"
uv sync --locked --extra dev
uv run --locked playwright install chromium
printf 'Установка готова. Заполните .env и state/main/hh-config.ini, затем запустите task full-activity.\n'
