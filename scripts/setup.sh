#!/usr/bin/env sh
# Локальная установка для Linux и macOS. Task должен быть установлен владельцем.
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
cd "$PROJECT_DIR"
uv sync --locked --extra dev
uv run playwright install chromium
uv run hhraiser setup
