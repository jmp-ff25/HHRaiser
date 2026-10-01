#!/usr/bin/env sh
# Локальная установка для Linux и macOS. Task должен быть установлен владельцем.
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
cd "$PROJECT_DIR"
export UV_CACHE_DIR="$PROJECT_DIR/.uv-cache"
uv sync --locked --extra dev
PLAYWRIGHT_BROWSERS_PATH="$PROJECT_DIR/state/main/playwright-browsers" \
  uv run --locked playwright install chromium
uv run --locked hhraiser setup
uv run --locked python -m hh_raiser.setup_ollama
